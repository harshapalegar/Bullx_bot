from flask import Flask, request, jsonify
from telegram import Bot
from telegram.utils.request import Request
from PIL import Image
from io import BytesIO
import re
import os
import logging
from datetime import datetime
import requests
from pymongo import MongoClient

# Import configurations directly
BOT_TOKEN = os.environ.get('BOT_TOKEN')
MONGODB_URI = os.environ.get('MONGODB_URI')
HELIUS_KEY = os.environ.get('HELIUS_KEY')

class UserPlan:
    FREE = "free"
    PREMIUM = "premium"

class UserLimits:
    FREE_WALLET_LIMIT = 3
    PREMIUM_WALLET_LIMIT = 10

# Add DatabaseManager class
class DatabaseManager:
    def __init__(self, db):
        self.db = db

    def ensure_user_exists(self, user_id: str, username: str = None) -> None:
        try:
            existing_user = self.db.users.find_one({"user_id": str(user_id)})
            if not existing_user:
                self.db.users.insert_one({
                    "user_id": str(user_id),
                    "username": username,
                    "plan": UserPlan.FREE,
                    "joined_date": datetime.now(),
                    "status": "active"
                })
                logger.info(f"Created new user: {user_id}")
        except Exception as e:
            logger.error(f"Error ensuring user exists: {e}")

    def get_user_stats(self, user_id: str) -> dict:
        try:
            active_wallets = self.db.wallets.count_documents({
                "user_id": str(user_id),
                "status": "active"
            })
            
            today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            transactions_today = self.db.messages.count_documents({
                "user": str(user_id),
                "datetime": {"$gte": today}
            })
            
            user_data = self.db.users.find_one({"user_id": str(user_id)}) or {
                "plan": UserPlan.FREE,
                "joined_date": datetime.now()
            }
            
            return {
                "active_wallets": active_wallets,
                "transactions_today": transactions_today,
                "plan": user_data.get("plan", UserPlan.FREE),
                "wallet_limit": UserLimits.PREMIUM_WALLET_LIMIT if user_data.get("plan") == UserPlan.PREMIUM else UserLimits.FREE_WALLET_LIMIT
            }
        except Exception as e:
            logger.error(f"Error getting user stats: {e}")
            return {
                "active_wallets": 0,
                "transactions_today": 0,
                "plan": UserPlan.FREE,
                "wallet_limit": UserLimits.FREE_WALLET_LIMIT
            }

# Add PremiumManager class
class PremiumManager:
    def __init__(self, db):
        self.db = db

    def is_premium(self, user_id: str) -> bool:
        try:
            user = self.db.users.find_one({"user_id": str(user_id)})
            return user and user.get("plan") == UserPlan.PREMIUM
        except Exception as e:
            logger.error(f"Error checking premium status: {e}")
            return False

def send_message_to_user(bot_token, user_id, message):
    try:
        request = Request(con_pool_size=8)
        bot = Bot(bot_token, request=request)
        bot.send_message(
            chat_id=user_id,
            text=message,
            parse_mode="Markdown",
            disable_web_page_preview=True)
        logger.info(f"Message sent to user {user_id}")
    except Exception as e:
        logger.error(f"Error sending message to user {user_id}: {e}")

def send_image_to_user(bot_token, user_id, message, image_url):
    try:
        request = Request(con_pool_size=8)
        bot = Bot(bot_token, request=request)
        image_bytes = get_image(image_url)
        bot.send_photo(
            chat_id=user_id,
            photo=image_bytes,
            caption=message,
            parse_mode="Markdown")
        logger.info(f"Image sent to user {user_id}")
    except Exception as e:
        logger.error(f"Error sending image to user {user_id}: {e}")
        send_message_to_user(bot_token, user_id, message)
    
def get_image(url):
    try:
        response = requests.get(url, timeout=10).content
        image = Image.open(BytesIO(response))
        image = image.convert('RGB')
        max_size = (800, 800)
        image.thumbnail(max_size, Image.LANCZOS)
        image_bytes = BytesIO()
        image.save(image_bytes, 'JPEG', quality=85)
        image_bytes.seek(0)
        return image_bytes
    except Exception as e:
        logger.error(f"Error getting image from {url}: {e}")
        raise

def format_wallet_address(match_obj):
    try:
        wallet_address = match_obj.group(0)
        return wallet_address[:4] + "..." + wallet_address[-4:]
    except Exception as e:
        logger.error(f"Error formatting wallet address: {e}")
        return match_obj.group(0)

def get_compressed_image(asset_id):
    try:
        url = f'https://rpc.helius.xyz/?api-key={HELIUS_KEY}'
        r_data = {
            "jsonrpc": "2.0",
            "id": "my-id",
            "method": "getAsset",
            "params": [asset_id]
        }
        r = requests.post(url, json=r_data, timeout=10)
        if r.status_code != 200:
            logger.error(f"Error response from Helius: {r.status_code}")
            return ''
        url_meta = r.json()['result']['content']['json_uri']
        r = requests.get(url=url_meta, timeout=10)
        if r.status_code != 200:
            logger.error(f"Error getting metadata: {r.status_code}")
            return ''
        return r.json()['image']
    except Exception as e:
        logger.error(f"Error getting compressed image for asset {asset_id}: {e}")
        return ''

def check_image(data):
    try:
        token_mint = ''
        for token in data[0]['tokenTransfers']:
            if 'NonFungible' in token['tokenStandard']:
                token_mint = token['mint']
        
        if len(token_mint) > 0:
            url = f"https://api.helius.xyz/v0/token-metadata?api-key={HELIUS_KEY}"
            nft_addresses = [token_mint]
            r_data = {
                "mintAccounts": nft_addresses,
                "includeOffChain": True,
                "disableCache": False,
            }

            r = requests.post(url=url, json=r_data, timeout=10)
            j = r.json()
            if 'metadata' not in j[0]['offChainMetadata']:
                return ''
            if 'image' not in j[0]['offChainMetadata']['metadata']:
                return ''
            image = j[0]['offChainMetadata']['metadata']['image']
            return image
        else:
            if 'compressed' in data[0]['events']:
                if 'assetId' in data[0]['events']['compressed'][0]:
                    asset_id = data[0]['events']['compressed'][0]['assetId']
                    try:
                        image = get_compressed_image(asset_id)
                        return image
                    except:
                        return ''
            return ''
    except Exception as e:
        logger.error(f"Error checking image: {e}")
        return ''

def create_message(data):
    try:
        tx_type = data[0]['type'].replace("_", " ")
        tx = data[0]['signature']
        source = data[0]['source']
        description = data[0]['description']

        accounts = []
        for inst in data[0]["instructions"]:
            accounts = accounts + inst["accounts"]
        
        if len(data[0]['tokenTransfers']) > 0:
            for token in data[0]['tokenTransfers']:
                accounts.append(token['fromUserAccount'])
                accounts.append(token['toUserAccount'])
            accounts = list(set(accounts))

        logger.info(f"Found accounts in transaction: {accounts}")
        image = check_image(data)
        
        found_docs = list(db.wallets.find({
            "address": {"$in": accounts},
            "status": "active"
        }))
        
        logger.info(f"Found wallet documents: {found_docs}")
        found_users = [i['user_id'] for i in found_docs]
        found_users = set(found_users)
        logger.info(f"Found users for notification: {found_users}")
        
        messages = []
        for user in found_users:
            if source != "SYSTEM_PROGRAM":
                message = f'*{tx_type}* on {source}'
            else:
                message = f'*{tx_type}*'
            if len(description) > 0:
                message = message + '\n\n' + data[0]['description']
                user_wallets = [i['address'] for i in found_docs if i['user_id']==user]
                
                for user_wallet in user_wallets:
                    if user_wallet not in message:
                        continue
                    wallet_info = next((doc for doc in found_docs if doc['address'] == user_wallet), None)
                    wallet_name = wallet_info.get('name', f"{user_wallet[:4]}...{user_wallet[-4:]}")
                    message = message.replace(user_wallet, f'*{wallet_name}*')

            formatted_text = re.sub(r'[A-Za-z0-9]{32,44}', format_wallet_address, message)
            formatted_text = formatted_text + f'\n[XRAY](https://xray.helius.xyz/tx/{tx}) | [Solscan](https://solscan.io/tx/{tx})'
            formatted_text = formatted_text.replace("#", "").replace("_", " ")
            messages.append({'user': user, 'text': formatted_text, 'image': image})
        
        return messages
    except Exception as e:
        logger.error(f"Error creating message: {e}")
        return []

# Set up logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# Initialize MongoDB and managers
try:
    client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=5000)
    client.admin.command('ping')
    db = client.sol_wallets
    logger.info("Successfully connected to MongoDB")
    
    # Initialize managers
    db_manager = DatabaseManager(db)
    premium_manager = PremiumManager(db)
    
except Exception as e:
    logger.error(f"Failed to connect to MongoDB: {e}")
    raise

app = Flask(__name__)

@app.route('/', methods=['GET'])
def health_check():
    try:
        client.admin.command('ping')
        mongo_status = "connected"
    except Exception as e:
        mongo_status = f"error: {str(e)}"
        
    return jsonify({
        "status": "ok",
        "timestamp": datetime.now().isoformat(),
        "mongo_status": mongo_status,
        "environment": {
            "MONGODB_URI": bool(MONGODB_URI),
            "BOT_TOKEN": bool(BOT_TOKEN),
            "HELIUS_KEY": bool(HELIUS_KEY)
        }
    }), 200

@app.route('/webhook-health', methods=['GET'])
def webhook_health():
    return jsonify({
        "status": "ok",
        "timestamp": datetime.now().isoformat(),
        "service": "solana-webhook"
    }), 200

@app.route('/wallet', methods=['POST'])
def handle_webhook():
    try:
        logger.info(f"Received webhook request: {request.headers}")
        if not request.is_json:
            logger.error("Invalid request: Not JSON")
            return jsonify({"error": "Invalid request"}), 400

        data = request.json
        logger.info(f"Webhook data: {data}")
        
        if not data:
            logger.error("Invalid request: Empty data")
            return jsonify({"error": "Empty data"}), 400
        
        messages = create_message(data)
        logger.info(f"Created messages: {messages}")

        for message in messages:
            try:
                db_entry = {
                    "user": message['user'],
                    "message": message['text'],
                    "datetime": datetime.now()
                }
                db.messages.insert_one(db_entry)

                if len(message['image']) > 0:
                    try:
                        send_image_to_user(BOT_TOKEN, message['user'], message['text'], message['image'])
                    except Exception as e:
                        logger.error(f"Error sending image, falling back to text: {e}")
                        send_message_to_user(BOT_TOKEN, message['user'], message['text'])    
                else:
                    send_message_to_user(BOT_TOKEN, message['user'], message['text'])
            except Exception as e:
                logger.error(f"Error processing message: {e}")

        logger.info('Webhook processed successfully')
        return jsonify({"status": "ok"}), 200

    except Exception as e:
        logger.error(f"Error handling webhook: {e}")
        return jsonify({"error": "Internal server error"}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5002))
    app.run(debug=False, host='0.0.0.0', port=port)
