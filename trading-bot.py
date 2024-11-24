import os
import time
import json
import logging
import requests
from solana.publickey import PublicKey
from solana.rpc.api import Client
from solana.keypair import Keypair
from solana.wallet import Wallet
import base58

# Setup logging
logging.basicConfig(
    format='%(asctime)s %(levelname)s: %(message)s',
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('trading-bot.log')
    ]
)
logger = logging.getLogger()

# API Setup
API_KEY = os.getenv('API_KEY')
BASE_URL = 'https://data.solanatracker.io/'
SESSION = requests.Session()
SESSION.headers.update({"x-api-key": API_KEY})

# Constants
SOL_ADDRESS = "So11111111111111111111111111111111111111112"
POSITION_FILE = "positions.json"
SOLD_POSITION_FILE = "sold_positions.json"

class TradingBot:
    def __init__(self):
        self.config = {
            "amount": float(os.getenv("AMOUNT")),
            "delay": int(os.getenv("DELAY")),
            "monitor_interval": int(os.getenv("MONITOR_INTERVAL")),
            "slippage": int(os.getenv("SLIPPAGE")),
            "priority_fee": float(os.getenv("PRIORITY_FEE")),
            "use_jito": os.getenv("JITO") == "true",
            "rpc_url": os.getenv("RPC_URL"),
            "min_liquidity": float(os.getenv("MIN_LIQUIDITY", 0)),
            "max_liquidity": float(os.getenv("MAX_LIQUIDITY", float("inf"))),
            "min_market_cap": float(os.getenv("MIN_MARKET_CAP", 0)),
            "max_market_cap": float(os.getenv("MAX_MARKET_CAP", float("inf"))),
            "min_risk_score": int(os.getenv("MIN_RISK_SCORE", 0)),
            "max_risk_score": int(os.getenv("MAX_RISK_SCORE", 10)),
            "require_social_data": os.getenv("REQUIRE_SOCIAL_DATA") == "true",
            "max_negative_pnl": float(os.getenv("MAX_NEGATIVE_PNL", float("-inf"))),
            "max_positive_pnl": float(os.getenv("MAX_POSITIVE_PNL", float("inf"))),
            "markets": os.getenv("MARKETS", "raydium,orca,pumpfun,moonshot,raydium-cpmm").split(",")
        }

        self.private_key = os.getenv("PRIVATE_KEY")
        self.positions = {}
        self.sold_positions = []
        self.seen_tokens = set()
        self.buying_tokens = set()
        self.selling_positions = set()

        self.connection = Client(self.config["rpc_url"])

    def initialize(self):
        self.keypair = Keypair.from_secret_key(base58.b58decode(self.private_key))
        self.wallet = Wallet(self.keypair)
        self.load_positions()
        self.load_sold_positions()

    def fetch_tokens(self):
        try:
            response = SESSION.get(BASE_URL + "/tokens/latest")
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            logger.error(f"Error fetching token data: {e}")
            return []

    def fetch_token_data(self, token_id):
        try:
            response = SESSION.get(f"{BASE_URL}/tokens/{token_id}")
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            logger.error(f"Error fetching token data for {token_id}: {e}")
            return None

    def filter_tokens(self, tokens):
        return [
            token for token in tokens
            if self.is_valid_token(token)
        ]

    def is_valid_token(self, token):
        pool = token['pools'][0]
        liquidity = pool['liquidity']['usd']
        market_cap = pool['marketCap']['usd']
        risk_score = token['risk']['score']
        has_social_data = any([token['token'].get(field) for field in ['twitter', 'telegram', 'website']])
        is_in_allowed_market = pool['market'] in self.config['markets']

        return (
            liquidity >= self.config['min_liquidity'] and
            liquidity <= self.config['max_liquidity'] and
            market_cap >= self.config['min_market_cap'] and
            market_cap <= self.config['max_market_cap'] and
            risk_score >= self.config['min_risk_score'] and
            risk_score <= self.config['max_risk_score'] and
            (not self.config['require_social_data'] or has_social_data) and
            is_in_allowed_market and
            token['token']['mint'] not in self.seen_tokens and
            token['token']['mint'] not in self.buying_tokens
        )

    def get_wallet_amount(self, wallet, mint, retries=3):
        time.sleep(5)
        for attempt in range(retries):
            try:
                token_account_info = self.connection.get_token_accounts_by_owner(wallet, mint=PublicKey(mint))
                if token_account_info['result']['value']:
                    balance = token_account_info['result']['value'][0]['account']['data']['parsed']['info']['tokenAmount']['uiAmount']
                    if balance > 0:
                        return balance
                time.sleep(10)
            except Exception as e:
                if attempt < retries - 1:
                    time.sleep(10)
                else:
                    logger.error(f"Error getting wallet amount for {mint}: {e}")
        return None

    def perform_swap(self, token, is_buy):
        logger.info(f"{'[BUYING]' if is_buy else '[SELLING]'} {token['token']['symbol']} [{token['token']['mint']}]")
        amount = self.config['amount']
        from_token, to_token = (SOL_ADDRESS, token['token']['mint']) if not is_buy else (token['token']['mint'], SOL_ADDRESS)

        # Perform swap logic (you need the actual swap method to execute the trade)
        try:
            swap_amount = amount if is_buy else self.positions[token['token']['mint']]['amount']
            # Here the actual swap should occur using the Solana API (stubbed in this example)
            logger.info(f"Swap transaction successful: {token['token']['symbol']}")
            return True
        except Exception as e:
            logger.error(f"Error performing {'buy' if is_buy else 'sell'} for {token['token']['mint']}: {e}")
            return False

    def buy_monitor(self):
        while True:
            tokens = self.fetch_tokens()
            filtered_tokens = self.filter_tokens(tokens)

            for token in filtered_tokens:
                if token['token']['mint'] not in self.positions and token['token']['mint'] not in self.buying_tokens:
                    self.buying_tokens.add(token['token']['mint'])
                    self.perform_swap(token, is_buy=True)

            time.sleep(self.config['delay'])

    def position_monitor(self):
        while True:
            for token_mint in list(self.positions.keys()):
                self.check_and_sell_position(token_mint)

            time.sleep(self.config['monitor_interval'])

    def check_and_sell_position(self, token_mint):
        if token_mint in self.selling_positions:
            return

        position = self.positions.get(token_mint)
        if not position:
            return

        token_data = self.fetch_token_data(token_mint)
        if not token_data:
            logger.error(f"Failed to fetch token data for {token_mint}")
            return

        current_price = token_data['pools'][0]['price']['quote']
        pnl_percentage = ((current_price - position['entry_price']) / position['entry_price']) * 100

        logger.info(f"PnL for position [{position['symbol']}] {pnl_percentage:.2f}%")

        if pnl_percentage <= self.config['max_negative_pnl'] or pnl_percentage >= self.config['max_positive_pnl']:
            self.selling_positions.add(token_mint)
            self.perform_swap(token_data, is_buy=False)

    def load_positions(self):
        try:
            with open(POSITION_FILE, 'r') as file:
                self.positions = json.load(file)
            logger.info(f"Loaded {len(self.positions)} positions from file")
        except FileNotFoundError:
            pass

    def save_positions(self):
        try:
            with open(POSITION_FILE, 'w') as file:
                json.dump(self.positions, file, indent=2)
            logger.info(f"Saved {len(self.positions)} positions to file")
        except Exception as e:
            logger.error(f"Error saving positions: {e}")

    def load_sold_positions(self):
        try:
            with open(SOLD_POSITION_FILE, 'r') as file:
                self.sold_positions = json.load(file)
            logger.info(f"Loaded {len(self.sold_positions)} sold positions from file")
        except FileNotFoundError:
            pass

    def save_sold_positions(self):
        try:
            with open(SOLD_POSITION_FILE, 'w') as file:
                json.dump(self.sold_positions, file, indent=2)
            logger.info(f"Saved {len(self.sold_positions)} sold positions to file")
        except Exception as e:
            logger.error(f"Error saving sold positions: {e}")

    def start(self):
        logger.info("Starting Trading Bot")
        self.initialize()

        # Run buying and selling loops concurrently
        from threading import Thread
        buy_thread = Thread(target=self.buy_monitor)
        position_thread = Thread(target=self.position_monitor)

        buy_thread.start()
        position_thread.start()

if __name__ == "__main__":
    bot = TradingBot()
    bot.start()
