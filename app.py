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

# Set up logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# Initialize MongoDB and utilities
try:
    client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=5000)
    client.admin.command('ping')
    db = client.sol_wallets
    logger.info("Successfully connected to MongoDB")
    
    # Initialize utility managers
    db_manager = DatabaseManager(db)
    premium_manager = PremiumManager(db)
    
except Exception as e:
    logger.error(f"Failed to connect to MongoDB: {e}")
    raise

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

# Rest of your existing app.py code remains the same...

# Flask app setup and routes
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
                # Save message to database
                db_entry = {
                    "user": message['user'],
                    "message": message['text'],
                    "datetime": datetime.now(),
                    "is_priority": message['priority']
                }
                db.messages.insert_one(db_entry)
                logger.info(f"Saved message to database: {db_entry}")

                # Send notification
                if len(message['image']) > 0:
                    try:
                        send_image_to_user(
                            BOT_TOKEN,
                            message['user'],
                            message['text'],
                            message['image'],
                            message['priority']
                        )
                    except Exception as e:
                        logger.error(f"Error sending image, falling back to text: {e}")
                        send_message_to_user(
                            BOT_TOKEN,
                            message['user'],
                            message['text'],
                            message['priority']
                        )    
                else:
                    send_message_to_user(
                        BOT_TOKEN,
                        message['user'],
                        message['text'],
                        message['priority']
                    )
            except Exception as e:
                logger.error(f"Error processing message: {e}")

        logger.info('Webhook processed successfully')
        return jsonify({"status": "ok"}), 200

    except Exception as e:
        logger.error(f"Error handling webhook: {e}")
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5002))
    app.run(debug=False, host='0.0.0.0', port=port)
