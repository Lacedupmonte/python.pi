import requests
import sqlite3
import pandas as pd
import numpy as np
import schedule
import time
from telegram import Bot
from sklearn.ensemble import IsolationForest

# Constants
COIN_BLACKLIST = set()  # Token addresses to blacklist
DEV_BLACKLIST = set()   # Developer addresses to blacklist
MIN_LIQUIDITY = 10000   # Minimum liquidity in USD
MAX_PRICE_CHANGE_24H = 1000  # Maximum 24h price change percentage
MIN_MARKET_CAP = 100000  # Minimum market cap in USD

# APIs
DEXSCREENER_API = "https://api.dexscreener.com/latest/dex"
POCKET_UNIVERSE_API = "https://api.pocketuniverse.ai/v1/check_wash_trading"
RUGCHECK_API = "https://api.rugcheck.xyz/v1/token_analysis"
SOLSCAN_API = "https://public-api.solscan.io"  # Solana blockchain explorer
TELEGRAM_BOT_TOKEN = "7738691138:AAE6sQc4SZyVdGCKcTH7W29p1ciPorrsL0w"  # Your Telegram Bot Token
TELEGRAM_CHAT_ID = "5979944526"  # Your Telegram Chat ID
RUGCHECK_API_KEY = "YOUR_RUGCHECK_API_KEY"  # Replace with your Rugcheck API key

# Initialize Telegram Bot
telegram_bot = Bot(token=TELEGRAM_BOT_TOKEN)

def send_telegram_message(message):
    """Send a message via Telegram."""
    telegram_bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message)

def fetch_token_data(token_address):
    """Fetch token data from DexScreener."""
    url = f"{DEXSCREENER_API}/tokens/solana/{token_address}"
    response = requests.get(url)
    if response.status_code == 200:
        return response.json()
    else:
        raise Exception(f"Failed to fetch token data: {response.status_code}")

def fetch_pair_data(pair_address):
    """Fetch pair data from DexScreener."""
    url = f"{DEXSCREENER_API}/pairs/solana/{pair_address}"
    response = requests.get(url)
    if response.status_code == 200:
        return response.json()
    else:
        raise Exception(f"Failed to fetch pair data: {response.status_code}")

def check_fake_volume_custom(token_data):
    """Custom algorithm to detect fake volume."""
    volume = token_data['volume']
    price_change = token_data['priceChange24h']
    if volume > 1_000_000 and abs(price_change) < 5:
        return True
    return False

def check_fake_volume_pocket_universe(token_address):
    """Use Pocket Universe API to detect fake volume."""
    headers = {"Authorization": "Bearer YOUR_POCKET_UNIVERSE_API_KEY"}
    params = {"token_address": token_address}
    response = requests.get(POCKET_UNIVERSE_API, headers=headers, params=params)
    if response.status_code == 200:
        return response.json().get('is_wash_trading', False)
    else:
        print(f"Failed to check fake volume: {response.status_code}")
        return False

def check_rugcheck(token_address):
    """Check if a token is marked as 'good' on Rugcheck."""
    headers = {"Authorization": f"Bearer {RUGCHECK_API_KEY}"}
    params = {"token_address": token_address}
    response = requests.get(RUGCHECK_API, headers=headers, params=params)
    if response.status_code == 200:
        result = response.json()
        # Check if the token is marked as "good"
        return result.get('risk_score', 100) < 50  # Example: Risk score below 50 is "good"
    else:
        print(f"Failed to check Rugcheck: {response.status_code}")
        return False

def check_bundled_supply(token_address):
    """Check if the token's supply is bundled (minted in a single transaction)."""
    url = f"{SOLSCAN_API}/token/{token_address}"
    response = requests.get(url)
    if response.status_code == 200:
        data = response.json()
        # Check if the token was minted in a single transaction
        if data.get('mintAuthority') == data.get('freezeAuthority'):
            return True
    return False

def fetch_and_filter_data():
    """Fetch and filter data from DexScreener."""
    # Example: Fetch top tokens on Solana
    chain_id = "solana"
    url = f"{DEXSCREENER_API}/tokens/{chain_id}"
    response = requests.get(url)
    if response.status_code == 200:
        data = response.json()
        filtered_data = []
        for token in data['pairs']:
            token_address = token['baseToken']['address']
            developer = "unknown"  # Replace with actual developer data if available
            if (token_address not in COIN_BLACKLIST and
                developer not in DEV_BLACKLIST and
                token['liquidity']['usd'] >= MIN_LIQUIDITY and
                abs(token['priceChange']['h24']) <= MAX_PRICE_CHANGE_24H and
                token['fdv'] >= MIN_MARKET_CAP):
                
                if (check_rugcheck(token_address) and
                    not check_bundled_supply(token_address) and
                    not check_fake_volume_custom(token) and
                    not check_fake_volume_pocket_universe(token_address)):
                    filtered_data.append(token)
                else:
                    COIN_BLACKLIST.add(token_address)
                    DEV_BLACKLIST.add(developer)
                    print(f"Blacklisted token: {token['baseToken']['name']} ({token_address})")
        return filtered_data
    else:
        raise Exception(f"Failed to fetch data: {response.status_code}")

def create_db():
    """Create the database."""
    conn = sqlite3.connect('dex_data.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS tokens (
            id TEXT PRIMARY KEY,
            name TEXT,
            symbol TEXT,
            address TEXT,
            developer TEXT,
            price REAL,
            market_cap REAL,
            liquidity REAL,
            volume REAL,
            price_change_24h REAL,
            is_rugged INTEGER,
            is_pumped INTEGER,
            is_tier1 INTEGER,
            listed_on_cex INTEGER,
            has_fake_volume INTEGER,
            rugcheck_status TEXT,
            is_bundled_supply INTEGER,
            timestamp DATETIME
        )
    ''')
    conn.commit()
    conn.close()

def save_to_db(data):
    """Save filtered data to the database."""
    conn = sqlite3.connect('dex_data.db')
    cursor = conn.cursor()
    for token in data:
        cursor.execute('''
            INSERT INTO tokens (id, name, symbol, address, developer, price, market_cap, liquidity, volume, price_change_24h, has_fake_volume, rugcheck_status, is_bundled_supply, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            token['baseToken']['address'], token['baseToken']['name'], token['baseToken']['symbol'], token['baseToken']['address'],
            "unknown", token['priceUsd'], token['fdv'], token['liquidity']['usd'],
            token['volume']['h24'], token['priceChange']['h24'],
            0, "good", 0, pd.Timestamp.now()
        ))
    conn.commit()
    conn.close()

def analyze_data():
    """Analyze data to detect anomalies."""
    conn = sqlite3.connect('dex_data.db')
    df = pd.read_sql_query("SELECT * FROM tokens", conn)
    conn.close()

    df = df[~df['address'].isin(COIN_BLACKLIST)]
    df = df[~df['developer'].isin(DEV_BLACKLIST)]
    df = df[df['has_fake_volume'] == 0]
    df = df[df['rugcheck_status'] == "good"]
    df = df[df['is_bundled_supply'] == 0]

    model = IsolationForest(contamination=0.01)
    df['anomaly'] = model.fit_predict(df[['price', 'market_cap', 'liquidity']])
    rugged_coins = df[df['anomaly'] == -1]

    rugged_coins.to_csv('rugged_coins.csv', index=False)
    return rugged_coins

def trade_with_trojan_bot(token):
    """Execute a trade using Trojan Bot via Telegram."""
    message = f"🚀 Buying {token['name']} ({token['symbol']}) at ${token['price']}"
    send_telegram_message(message)
    # Add Trojan Bot trading logic here (e.g., sending commands to Trojan Bot via Telegram)

def run_bot():
    """Run the bot."""
    data = fetch_and_filter_data()
    save_to_db(data)
    analysis_results = analyze_data()
    for _, token in analysis_results.iterrows():
        trade_with_trojan_bot(token)
    send_telegram_message("✅ Bot cycle completed.")

# Schedule the bot to run every hour
schedule.every().hour.do(run_bot)

if __name__ == "__main__":
    create_db()
    while True:
        schedule.run_pending()
        time.sleep(1)
