import os
import requests
import base58
from datetime import datetime, timedelta
from pymongo import MongoClient

# Environment variables
HELIUS_KEY = os.environ.get('HELIUS_KEY')
MONGODB_URI = os.environ.get('MONGODB_URI')

client = MongoClient(MONGODB_URI)
db = client.sol_wallets
wallets_collection = db.wallets

def is_solana_wallet_address(address):
    """
    Validate if the given string is a valid Solana wallet address.
    """
    try:
        # Check if the address is a valid base58 string and decodes to 32 bytes
        decoded = base58.b58decode(address)
        return len(decoded) == 32
    except Exception:
        return False

def check_wallet_transactions(address):
    """
    Check number of transactions for a wallet in the last 24 hours.
    Returns (bool, float) - success status and number of transactions per day.
    """
    try:
        # Calculate timestamps for the last 24 hours
        end_time = datetime.now()
        start_time = end_time - timedelta(days=1)
        
        # Convert to Unix timestamps
        start_time_unix = int(start_time.timestamp())
        end_time_unix = int(end_time.timestamp())
        
        # Get transactions from Helius
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
        
        # Check if number of transactions is less than 50 per day
        return num_transactions < 50, num_transactions
        
    except Exception as e:
        print(f"Error checking transactions: {e}")
        return False, 0

def wallet_count_for_user(user_id):
    """
    Get the number of active wallets for a user.
    """
    return wallets_collection.count_documents({
        "user_id": str(user_id),
        "status": "active"
    })

def get_webhook(webhook_id):
    """
    Get existing webhook configuration from Helius.
    Returns (success, webhook_id, addresses).
    """
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
    """
    Add a new address to existing webhook.
    """
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
    """
    Remove an address from existing webhook.
    """
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
