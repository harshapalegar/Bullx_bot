import logging
import os
import requests
import base58
from telegram import Update, ForceReply
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackQueryHandler
from telegram.ext import ConversationHandler
from pymongo import MongoClient
from datetime import datetime, timedelta

# Environment variables
MONGODB_URI = os.environ.get('MONGODB_URI')
BOT_TOKEN = os.environ.get('BOT_TOKEN')
TOKEN = BOT_TOKEN
HELIUS_KEY = os.environ.get('HELIUS_KEY')
HELIUS_WEBHOOK_ID = os.environ.get('HELIUS_WEBHOOK_ID')

ADDING_WALLET, DELETING_WALLET = range(2)
client = MongoClient(MONGODB_URI)
db = client.sol_wallets
wallets_collection = db.wallets

# Set up logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# Bot tools functions
def is_solana_wallet_address(address):
    try:
        decoded = base58.b58decode(address)
        return len(decoded) == 32
    except Exception:
        return False

def check_wallet_transactions(address):
    try:
        end_time = datetime.now()
        start_time = end_time - timedelta(days=1)
        
        start_time_unix = int(start_time.timestamp())
        end_time_unix = int(end_time.timestamp())
        
        url = f"https://api.helius.xyz/v0/addresses/{address}/transactions?api-key={HELIUS_KEY}"
        params = {
            "until": str(end_time_unix),
            "from": str(start_time_unix)
        }
        response = requests.get(url, params=params)
        
        if response.status_code != 200:
            return False, 0
            
        transactions = response.json()
        num_transactions = len(transactions)
        
        return num_transactions < 50, num_transactions
        
    except Exception as e:
        print(f"Error checking transactions: {e}")
        return False, 0

def wallet_count_for_user(user_id):
    return wallets_collection.count_documents({
        "user_id": str(user_id),
        "status": "active"
    })

def get_webhook(webhook_id):
    try:
        url = f"https://api.helius.xyz/v0/webhooks?api-key={HELIUS_KEY}"
        response = requests.get(url)
        
        if response.status_code != 200:
            return False, None, []
            
        webhooks = response.json()
        for webhook in webhooks:
            if webhook['webhookID'] == webhook_id:
                return True, webhook_id, webhook.get('accountAddresses', [])
                
        return False, None, []
        
    except Exception as e:
        print(f"Error getting webhook: {e}")
        return False, None, []

def add_webhook(user_id, address, webhook_id, existing_addresses):
    try:
        if address in existing_addresses:
            return True
            
        url = f"https://api.helius.xyz/v0/webhooks/{webhook_id}?api-key={HELIUS_KEY}"
        
        new_addresses = existing_addresses + [address]
        data = {
            "accountAddresses": new_addresses,
            "webhookURL": os.environ.get('WEBHOOK_URL', 'http://your-koyeb-url/wallet')
        }
        
        response = requests.put(url, json=data)
        return response.status_code == 200
        
    except Exception as e:
        print(f"Error adding webhook: {e}")
        return False

def delete_webhook(user_id, address, webhook_id, existing_addresses):
    try:
        if address not in existing_addresses:
            return True
            
        url = f"https://api.helius.xyz/v0/webhooks/{webhook_id}?api-key={HELIUS_KEY}"
        
        new_addresses = [addr for addr in existing_addresses if addr != address]
        data = {
            "accountAddresses": new_addresses,
            "webhookURL": os.environ.get('WEBHOOK_URL', 'http://your-koyeb-url/wallet')
        }
        
        response = requests.put(url, json=data)
        return response.status_code == 200
        
    except Exception as e:
        print(f"Error deleting webhook: {e}")
        return False

# Bot message handlers
def welcome_message() -> str:
    message = (
        "🤖 Ahoy there, Solana Wallet Wrangler! Welcome to Solana Wallet Xray Bot! 🤖\n\n"
        "I'm your trusty sidekick, here to help you juggle those wallets and keep an eye on transactions.\n"
        "Once you've added your wallets, you can sit back and relax, as I'll swoop in with a snappy notification and a brief transaction summary every time your wallet makes a move on Solana. 🚀\n"
        "Have a blast using the bot! 😄\n\n"
        "Ready to rumble? Use the commands below and follow the prompts:"
    )
    return message

[... Rest of the bot.py file remains the same ...]
