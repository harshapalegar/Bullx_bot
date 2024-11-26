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
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Environment variables
BOT_TOKEN = os.environ.get('BOT_TOKEN')
MONGODB_URI = os.environ.get('MONGODB_URI')
HELIUS_KEY = os.environ.get('HELIUS_KEY')

class UserPlan:
    FREE = "free"
    PREMIUM = "premium"

class UserLimits:
    FREE_WALLET_LIMIT = 3
    PREMIUM_WALLET_LIMIT = 10

# Set up logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# Initialize MongoDB
try:
    client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=5000)
    client.admin.command('ping')
    db = client.sol_wallets
    logger.info("Successfully connected to MongoDB")
except Exception as e:
    logger.error(f"Failed to connect to MongoDB: {e}")
    raise

def format_number(number, decimals=2):
    try:
        if number >= 1_000_000:
            return f"{number/1_000_000:.{decimals}f}M"
        elif number >= 1_000:
            return f"{number/1_000:.{decimals}f}K"
        else:
            return f"{number:.{decimals}f}"
    except:
        return str(number)

def get_token_info(token_address):
    try:
        url = f"https://api.helius.xyz/v0/token-metadata?api-key={HELIUS_KEY}"
        response = requests.post(url, json={"mintAccounts": [token_address]})
        if response.status_code == 200:
            data = response.json()[0]
            return {
                "symbol": data.get("symbol", ""),
                "name": data.get("name", ""),
                "decimals": data.get("decimals", 9)
            }
        return None
    except Exception as e:
        logger.error(f"Error getting token info: {e}")
        return None

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

def get_token_price(token_address):
    try:
        url = f"https://api.helius.xyz/v0/token-metadata?api-key={HELIUS_KEY}"
        response = requests.post(url, json={"mintAccounts": [token_address]})
        if response.status_code == 200:
            data = response.json()[0]
            if 'price' in data:
                return data['price']
        return None
    except Exception as e:
        logger.error(f"Error getting token price: {e}")
        return None

def get_compressed_image(asset_id):
    try:
        url = f'https://rpc.helius.xyz/?api-key={HELIUS_KEY}'
        r_data = {
            "jsonrpc": "2.0",
            "id": "my-id",
            "method": "getAsset",
            "params": [asset_id]
        }
        response = requests.post(url, json=r_data, timeout=10)
        if response.status_code != 200:
            logger.error(f"Error response from Helius: {response.status_code}")
            return ''
        url_meta = response.json()['result']['content']['json_uri']
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

def process_token_transfers(transfers, tx_type):
    try:
        result = {
            'amount_in': 0,
            'amount_out': 0,
            'token_in': None,
            'token_out': None,
            'usd_value': 0
        }
        
        for transfer in transfers:
            amount = float(transfer.get('tokenAmount', 0))
            token_info = get_token_info(transfer.get('mint', ''))
            
            if token_info:
                if transfer.get('tokenStandard') == 'Fungible':
                    price = get_token_price(transfer.get('mint', ''))
                    if price:
                        result['usd_value'] += amount * price

                    if 'symbol' in token_info:
                        if tx_type == 'SWAP':
                            if not result['token_in']:
                                result['token_in'] = token_info
                                result['amount_in'] = amount
                            else:
                                result['token_out'] = token_info
                                result['amount_out'] = amount
        
        return result
    except Exception as e:
        logger.error(f"Error processing token transfers: {e}")
        return None

def create_message(data):
    try:
        logger.info(f"Processing transaction data: {data}")
        
        tx_type = data[0]['type'].replace("_", " ")
        tx = data[0]['signature']
        source = data[0]['source']
        description = data[0]['description']

        # Process token transfers
        token_data = process_token_transfers(data[0].get('tokenTransfers', []), tx_type)
        
        # Build message based on transaction type
        if tx_type == "SWAP":
            message = f"{'🔴 SELL' if source == 'RAYDIUM' else '🟢 BUY'} "
            if token_data and token_data['token_in'] and token_data['token_out']:
                message += (
                    f"{token_data['token_in']['symbol']} on {source}\n\n"
                    f"🔹 Amount: {format_number(token_data['amount_in'])} "
                    f"{token_data['token_in']['symbol']}"
                )
                if token_data['usd_value'] > 0:
                    message += f" (${format_number(token_data['usd_value'])})"
                message += (
                    f"\n🔹 For: {format_number(token_data['amount_out'])} "
                    f"{token_data['token_out']['symbol']}"
                )
                
                # Add token links
                token_mint = data[0]['tokenTransfers'][0].get('mint', '')
                if token_mint:
                    message += (
                        f"\n\n🔗 Links:\n"
                        f"• [Birdeye](https://birdeye.so/token/{token_mint})\n"
                        f"• [DexScreener](https://dexscreener.com/solana/{token_mint})\n"
                        f"• [Solscan](https://solscan.io/token/{token_mint})"
                    )
        elif tx_type == "NFT SALE" or tx_type == "NFT PURCHASE":
            symbol = ""
            amount = 0
            for transfer in data[0]['tokenTransfers']:
                if transfer.get('tokenStandard') == 'NonFungible':
                    symbol = transfer.get('symbol', '')
                elif transfer.get('tokenStandard') == 'Fungible':
                    amount = float(transfer.get('tokenAmount', 0))
            
            message = (
                f"{'🔴 SOLD' if tx_type == 'NFT SALE' else '🟢 BOUGHT'} "
                f"{symbol} on {source}\n"
                f"💰 Price: {format_number(amount)} SOL"
            )
        else:
            message = f"*{tx_type}* on {source}\n\n"
            if description:
                message += description

        # Add transaction links
        message += f"\n\n[XRAY](https://xray.helius.xyz/tx/{tx}) | [Solscan](https://solscan.io/tx/{tx})"

        # Process accounts
        accounts = []
        for inst in data[0]["instructions"]:
            accounts.extend(inst["accounts"])
        
        if data[0]['tokenTransfers']:
            for token in data[0]['tokenTransfers']:
                accounts.append(token['fromUserAccount'])
                accounts.append(token['toUserAccount'])
            accounts = list(set(accounts))

        image = check_image(data)
        
        # Find affected wallets
        found_docs = list(db.wallets.find({
            "address": {"$in": accounts},
            "status": "active"
        }))
        
        found_users = list(set(doc['user_id'] for doc in found_docs))
        logger.info(f"Found users for notification: {found_users}")
        
        messages = []
        for user in found_users:
            user_message = message
            user_wallets = [doc for doc in found_docs if doc['user_id'] == user]
            
            # Replace wallet addresses with names
            for wallet in user_wallets:
                if wallet['address'] in user_message:
                    wallet_name = wallet.get('name', f"{wallet['address'][:4]}...{wallet['address'][-4:]}")
                    user_message = user_message.replace(
                        wallet['address'], 
                        f"*{wallet_name}*"
                    )

            # Format remaining addresses
            user_message = re.sub(r'[A-Za-z0-9]{32,44}', format_wallet_address, user_message)
            
            messages.append({
                'user': user,
                'text': user_message,
                'image': image,
                'priority': db.users.find_one({"user_id": user, "plan": UserPlan.PREMIUM}) is not None
            })
        
        return messages
    except Exception as e:
        logger.error(f"Error creating message: {e}")
        logger.error(f"Data that caused error: {data}")
        return []

# Initialize Flask app
app = Flask(__name__)

@app.route('/', methods=['GET'])
def health_check():
    """Health check endpoint"""
    try:
        # Test MongoDB connection
        client.admin.command('ping')
        mongo_status = "connected"
        
        # Test Helius API
        helius_status = "connected" if HELIUS_KEY else "not configured"
        
        return jsonify({
            "status": "ok",
            "timestamp": datetime.now().isoformat(),
            "mongo_status": mongo_status,
            "helius_status": helius_status,
            "environment": {
                "MONGODB_URI": bool(MONGODB_URI),
                "BOT_TOKEN": bool(BOT_TOKEN),
                "HELIUS_KEY": bool(HELIUS_KEY)
            }
        }), 200
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return jsonify({
            "status": "error",
            "error": str(e),
            "timestamp": datetime.now().isoformat()
        }), 500

@app.route('/webhook-health', methods=['GET'])
def webhook_health():
    """Webhook specific health check"""
    try:
        # Get some basic stats
        total_users = db.users.count_documents({"status": "active"})
        total_wallets = db.wallets.count_documents({"status": "active"})
        premium_users = db.users.count_documents({"plan": UserPlan.PREMIUM})
        
        return jsonify({
            "status": "ok",
            "timestamp": datetime.now().isoformat(),
            "service": "solana-webhook",
            "stats": {
                "total_users": total_users,
                "total_wallets": total_wallets,
                "premium_users": premium_users
            }
        }), 200
    except Exception as e:
        logger.error(f"Webhook health check failed: {e}")
        return jsonify({
            "status": "error",
            "error": str(e)
        }), 500

@app.route('/test-message', methods=['POST'])
def test_message():
    """Endpoint to test message formatting"""
    try:
        if not request.is_json:
            return jsonify({"error": "Request must be JSON"}), 400

        data = request.json
        messages = create_message(data)
        
        return jsonify({
            "status": "ok",
            "messages": messages
        }), 200
    except Exception as e:
        logger.error(f"Test message failed: {e}")
        return jsonify({
            "status": "error",
            "error": str(e)
        }), 500

@app.route('/wallet', methods=['POST'])
def handle_webhook():
    """Main webhook endpoint"""
    try:
        logger.info(f"Received webhook request: {request.headers}")
        
        # Validate request
        if not request.is_json:
            logger.error("Invalid request: Not JSON")
            return jsonify({"error": "Invalid request"}), 400

        data = request.json
        if not data:
            logger.error("Invalid request: Empty data")
            return jsonify({"error": "Empty data"}), 400

        logger.info(f"Processing webhook data: {data}")
        
        # Create messages for all affected users
        messages = create_message(data)
        logger.info(f"Created messages: {messages}")

        # Send messages to users
        for message in messages:
            try:
                # Save message to database
                db_entry = {
                    "user": message['user'],
                    "message": message['text'],
                    "datetime": datetime.now(),
                    "priority": message.get('priority', False),
                    "tx_signature": data[0].get('signature', ''),
                    "tx_type": data[0].get('type', '')
                }
                db.messages.insert_one(db_entry)
                logger.info(f"Saved message to database: {db_entry['_id']}")

                # Send notification
                try:
                    if message.get('image'):
                        send_image_to_user(
                            BOT_TOKEN,
                            message['user'],
                            message['text'],
                            message['image']
                        )
                    else:
                        send_message_to_user(
                            BOT_TOKEN,
                            message['user'],
                            message['text']
                        )
                except Exception as e:
                    logger.error(f"Error sending notification: {e}")
                    # Try sending as text if image fails
                    if message.get('image'):
                        send_message_to_user(
                            BOT_TOKEN,
                            message['user'],
                            message['text']
                        )
                        
            except Exception as e:
                logger.error(f"Error processing message for user {message['user']}: {e}")
                continue

        logger.info('Webhook processed successfully')
        return jsonify({
            "status": "ok",
            "messages_sent": len(messages)
        }), 200

    except Exception as e:
        logger.error(f"Error handling webhook: {e}")
        return jsonify({
            "status": "error",
            "error": str(e)
        }), 500

# Error handlers
@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": "Not found"}), 404

@app.errorhandler(500)
def server_error(e):
    return jsonify({"error": "Internal server error"}), 500

if __name__ == '__main__':
    # Get port from environment variable or use default
    port = int(os.environ.get('PORT', 5002))
    
    # Verify required environment variables
    required_vars = ['MONGODB_URI', 'BOT_TOKEN', 'HELIUS_KEY']
    missing_vars = [var for var in required_vars if not os.environ.get(var)]
    if missing_vars:
        raise EnvironmentError(f"Missing required environment variables: {', '.join(missing_vars)}")
    
    # Start Flask app
    logger.info(f"Starting server on port {port}")
    app.run(
        host='0.0.0.0',
        port=port,
        debug=False
    )
