import logging
import os
import requests
import base58
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import (
    Update, ForceReply, Bot, ParseMode, ReplyKeyboardRemove, 
    CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
)
from telegram.ext import (
    Updater, CommandHandler, MessageHandler, Filters, CallbackContext,
    CallbackQueryHandler, ConversationHandler
)
from telegram.error import BadRequest, Unauthorized, TimedOut
from pymongo import MongoClient
from datetime import datetime, timedelta
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Environment variables
BOT_TOKEN = os.environ.get('BOT_TOKEN')
MONGODB_URI = os.environ.get('MONGODB_URI')
HELIUS_KEY = os.environ.get('HELIUS_KEY')
HELIUS_WEBHOOK_ID = os.environ.get('HELIUS_WEBHOOK_ID')

# Constants
ADMIN_IDS = ['your_telegram_id']  # Replace with your Telegram ID
ADDING_WALLET, ADDING_NAME, DELETING_WALLET = range(3)

# User Plan classes
class UserPlan:
    FREE = "free"
    PREMIUM = "premium"

class UserLimits:
    FREE_WALLET_LIMIT = 3
    PREMIUM_WALLET_LIMIT = 10

# Set up logging first
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# Health check handler
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b"OK")
        
    def log_message(self, format, *args):
        pass

def run_health_check_server():
    try:
        server_address = ('', 8000)
        httpd = HTTPServer(server_address, HealthCheckHandler)
        logger.info("Health check server started on port 8000")
        httpd.serve_forever()
    except Exception as e:
        logger.error(f"Error in health check server: {e}")

# Start health check server
health_check_thread = threading.Thread(target=run_health_check_server, daemon=True)
health_check_thread.start()

# Database Manager Class
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

    def add_wallet(self, user_id: str, wallet_address: str, wallet_name: str) -> bool:
        try:
            existing_wallet = self.db.wallets.find_one({
                "user_id": str(user_id),
                "address": wallet_address,
                "status": "active"
            })
            
            if existing_wallet:
                return False
                
            self.db.wallets.insert_one({
                "user_id": str(user_id),
                "address": wallet_address,
                "name": wallet_name,
                "datetime": datetime.now(),
                "status": "active"
            })
            logger.info(f"Added wallet {wallet_address} for user {user_id}")
            return True
        except Exception as e:
            logger.error(f"Error adding wallet: {e}")
            return False

    def delete_wallet(self, user_id: str, wallet_name: str) -> bool:
        try:
            result = self.db.wallets.update_one(
                {
                    "user_id": str(user_id),
                    "name": wallet_name,
                    "status": "active"
                },
                {"$set": {"status": "inactive"}}
            )
            logger.info(f"Deleted wallet {wallet_name} for user {user_id}")
            return result.modified_count > 0
        except Exception as e:
            logger.error(f"Error deleting wallet: {e}")
            return False

    def get_user_wallets(self, user_id: str) -> list:
        try:
            return list(self.db.wallets.find({
                "user_id": str(user_id),
                "status": "active"
            }))
        except Exception as e:
            logger.error(f"Error getting user wallets: {e}")
            return []

# Premium Manager Class
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

    def upgrade_to_premium(self, user_id: str) -> bool:
        try:
            result = self.db.users.update_one(
                {"user_id": str(user_id)},
                {
                    "$set": {
                        "plan": UserPlan.PREMIUM,
                        "premium_since": datetime.now()
                    }
                }
            )
            return result.modified_count > 0
        except Exception as e:
            logger.error(f"Error upgrading to premium: {e}")
            return False

# Admin System Class
class AdminSystem:
    def __init__(self, db):
        self.db = db
        self._admin_ids = ADMIN_IDS

    def is_admin(self, user_id: str) -> bool:
        return str(user_id) in self._admin_ids

    def get_system_stats(self):
        try:
            today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            return {
                "total_users": len(set(w["user_id"] for w in self.db.wallets.find({"status": "active"}))),
                "total_wallets": self.db.wallets.count_documents({"status": "active"}),
                "premium_users": self.db.users.count_documents({"plan": UserPlan.PREMIUM}),
                "active_today": len(set(m["user"] for m in self.db.messages.find({"datetime": {"$gte": today}}))),
                "transactions_today": self.db.messages.count_documents({"datetime": {"$gte": today}})
            }
        except Exception as e:
            logger.error(f"Error getting system stats: {e}")
            return {
                "total_users": 0,
                "total_wallets": 0,
                "premium_users": 0,
                "active_today": 0,
                "transactions_today": 0
            }

# Initialize MongoDB and managers
try:
    client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=5000)
    client.admin.command('ping')
    db = client.sol_wallets
    logger.info("Successfully connected to MongoDB")
    
    # Initialize managers
    db_manager = DatabaseManager(db)
    premium_manager = PremiumManager(db)
    admin_system = AdminSystem(db)
    
except Exception as e:
    logger.error(f"Failed to connect to MongoDB: {e}")
    raise

# Utility functions
def is_solana_wallet_address(address: str) -> bool:
    try:
        decoded = base58.b58decode(address)
        return len(decoded) == 32
    except Exception as e:
        logger.error(f"Error validating Solana address: {e}")
        return False

def get_webhook(webhook_id: str):
    try:
        url = f"https://api.helius.xyz/v0/webhooks?api-key={HELIUS_KEY}"
        response = requests.get(url, timeout=10)
        
        if response.status_code != 200:
            logger.error(f"Helius API error: {response.status_code}")
            return True, webhook_id, []
            
        webhooks = response.json()
        for webhook in webhooks:
            if webhook['webhookID'] == webhook_id:
                return True, webhook_id, webhook.get('accountAddresses', [])
                
        return True, webhook_id, []
        
    except Exception as e:
        logger.error(f"Error getting webhook: {e}")
        return True, webhook_id, []

def add_webhook(user_id: str, address: str, webhook_id: str, existing_addresses: list) -> bool:
    try:
        if address in existing_addresses:
            return True
            
        url = f"https://api.helius.xyz/v0/webhooks/{webhook_id}?api-key={HELIUS_KEY}"
        
        webhook_url = os.environ.get('WEBHOOK_URL')
        if not webhook_url:
            logger.warning("WEBHOOK_URL not set")
            return True

        new_addresses = existing_addresses + [address]
        data = {
            "accountAddresses": new_addresses,
            "webhookURL": webhook_url
        }
        
        response = requests.put(url, json=data, timeout=10)
        return True
        
    except Exception as e:
        logger.error(f"Error adding webhook for address {address}: {e}")
        return True

# Command Handlers
def error_handler(update: Update, context: CallbackContext) -> None:
    logger.error(f"Update {update} caused error {context.error}")
    try:
        if isinstance(context.error, (BadRequest, Unauthorized)):
            return
        if update.message:
            update.message.reply_text(
                "Sorry, something went wrong. Please try again later! 🔧"
            )
    except Exception as e:
        logger.error(f"Error in error handler: {e}")

def welcome_message() -> str:
    message = (
        "🤖 Welcome to Solana Wallet Tracker Bot! 🤖\n\n"
        "I'll help you track your Solana wallets and notify you of transactions.\n\n"
        "*Available Commands:*\n"
        "/add - Add a new wallet\n"
        "/show - Show your wallets\n"
        "/delete - Delete a wallet\n"
        "/stats - View your statistics\n"
        "/premium - Learn about premium features\n\n"
        "Let's get started! 🚀"
    )
    return message

def start(update: Update, context: CallbackContext) -> None:
    try:
        user_id = str(update.effective_user.id)
        username = update.effective_user.username
        
        # Ensure user exists in database
        db_manager.ensure_user_exists(user_id, username)
        
        keyboard = [
            [
                InlineKeyboardButton("➕ Add Wallet", callback_data="add_wallet"),
                InlineKeyboardButton("👀 Show Wallets", callback_data="show_wallets")
            ],
            [
                InlineKeyboardButton("📊 Statistics", callback_data="stats"),
                InlineKeyboardButton("⭐️ Premium", callback_data="premium")
            ]
        ]
        
        # Add admin button if user is admin
        if admin_system.is_admin(user_id):
            keyboard.append([InlineKeyboardButton("🔐 Admin Panel", callback_data="admin")])

        reply_markup = InlineKeyboardMarkup(keyboard)
        
        if update.message:
            update.message.reply_text(
                welcome_message(),
                reply_markup=reply_markup,
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            update.callback_query.edit_message_text(
                welcome_message(),
                reply_markup=reply_markup,
                parse_mode=ParseMode.MARKDOWN
            )
    except Exception as e:
        logger.error(f"Error in start command: {e}")
        if update.message:
            update.message.reply_text("Sorry, something went wrong. Please try /start again!")

def cancel(update: Update, context: CallbackContext) -> int:
    update.message.reply_text(
        "Operation cancelled. Send /start to begin again.",
        reply_markup=ReplyKeyboardRemove()
    )
    return ConversationHandler.END

def handle_delete_callback(update: Update, context: CallbackContext) -> int:
    try:
        query = update.callback_query
        user_id = str(update.effective_user.id)
        logger.info(f"Processing delete callback for user {user_id}")
        
        if query.data == "start":
            start(update, context)
            return ConversationHandler.END
            
        wallet_name = query.data.replace("delete_", "")
        logger.info(f"Attempting to delete wallet {wallet_name}")
        
        success = db_manager.delete_wallet(user_id, wallet_name)
        
        if success:
            message = f"Successfully deleted wallet '*{wallet_name}*'! 🗑️"
            logger.info(f"Successfully deleted wallet {wallet_name}")
        else:
            message = "Couldn't find that wallet. It might have been already deleted! 🤔"
            logger.warning(f"Failed to delete wallet {wallet_name}")
            
        keyboard = [
            [
                InlineKeyboardButton("👀 Show Wallets", callback_data="show_wallets"),
                InlineKeyboardButton("🔙 Main Menu", callback_data="start")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        query.answer()
        query.edit_message_text(
            message,
            reply_markup=reply_markup,
            parse_mode=ParseMode.MARKDOWN
        )
        return ConversationHandler.END
        
    except Exception as e:
        logger.error(f"Error handling delete callback: {e}")
        try:
            query.edit_message_text(
                "Sorry, something went wrong. Please try again! 🔧",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Main Menu", callback_data="start")
                ]])
            )
        except Exception as inner_e:
            logger.error(f"Error sending error message: {inner_e}")
        return ConversationHandler.END

def show_wallets(update: Update, context: CallbackContext) -> None:
    try:
        user_id = str(update.effective_user.id)
        wallets = db_manager.get_user_wallets(user_id)
        
        if not wallets:
            message = (
                "You don't have any wallets yet! 🏦\n\n"
                "Use /add to add your first wallet!"
            )
            keyboard = [[InlineKeyboardButton("➕ Add Wallet", callback_data="add_wallet")]]
        else:
            stats = db_manager.get_user_stats(user_id)
            message = (
                f"*Your Wallets* ({len(wallets)}/{stats['wallet_limit']}):\n\n"
            )
            for wallet in wallets:
                message += f"*{wallet['name']}*\n`{wallet['address']}`\n\n"
                
            keyboard = [
                [
                    InlineKeyboardButton("➕ Add", callback_data="add_wallet"),
                    InlineKeyboardButton("🗑 Delete", callback_data="delete_wallet")
                ],
                [InlineKeyboardButton("🔙 Main Menu", callback_data="start")]
            ]
            
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        if isinstance(update.callback_query, CallbackQuery):
            update.callback_query.answer()
            update.callback_query.edit_message_text(
                message,
                reply_markup=reply_markup,
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            update.message.reply_text(
                message,
                reply_markup=reply_markup,
                parse_mode=ParseMode.MARKDOWN
            )
            
    except Exception as e:
        logger.error(f"Error showing wallets: {e}")
        try:
            error_text = "Sorry, something went wrong. Please try again!"
            if isinstance(update.callback_query, CallbackQuery):
                update.callback_query.edit_message_text(error_text)
            else:
                update.message.reply_text(error_text)
        except:
            pass

def delete_wallet_start(update: Update, context: CallbackContext) -> int:
    try:
        user_id = str(update.effective_user.id)
        wallets = db_manager.get_user_wallets(user_id)
        
        if not wallets:
            message = "You don't have any wallets to delete! 🤷‍♂️"
            keyboard = [[InlineKeyboardButton("🔙 Main Menu", callback_data="start")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            if isinstance(update.callback_query, CallbackQuery):
                update.callback_query.answer()
                update.callback_query.edit_message_text(
                    message,
                    reply_markup=reply_markup
                )
            else:
                update.message.reply_text(message, reply_markup=reply_markup)
            return ConversationHandler.END

        message = "*Select wallet to delete:*\n\n"
        keyboard = []
        
        for wallet in wallets:
            name = wallet['name']
            address = wallet['address']
            button_text = f"{name} ({address[:4]}...{address[-4:]})"
            keyboard.append([InlineKeyboardButton(
                text=button_text,
                callback_data=f"delete_{name}"
            )])
            
        keyboard.append([InlineKeyboardButton("🔙 Cancel", callback_data="start")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        if isinstance(update.callback_query, CallbackQuery):
            update.callback_query.answer()
            update.callback_query.edit_message_text(
                message,
                reply_markup=reply_markup,
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            update.message.reply_text(
                message,
                reply_markup=reply_markup,
                parse_mode=ParseMode.MARKDOWN
            )
            
        return DELETING_WALLET
        
    except Exception as e:
        logger.error(f"Error in delete_wallet_start: {e}")
        return ConversationHandler.END

def add_wallet_start(update: Update, context: CallbackContext) -> int:
    try:
        user_id = str(update.effective_user.id)
        stats = db_manager.get_user_stats(user_id)
        
        if stats['active_wallets'] >= stats['wallet_limit']:
            message = (
                f"❗️ You've reached the {stats['plan']} plan wallet limit!\n\n"
                f"Current limit: {stats['wallet_limit']} wallets\n"
            )
            if stats['plan'] == UserPlan.FREE:
                message += "\nUpgrade to Premium for more wallets! Use /premium"
                
            if isinstance(update.callback_query, CallbackQuery):
                update.callback_query.answer()
                update.callback_query.edit_message_text(message)
            else:
                update.message.reply_text(message)
            return ConversationHandler.END
            
        message = (
            "Please send me the Solana wallet address you want to add 🏦\n"
            "Or send /cancel to cancel"
        )
        
        if isinstance(update.callback_query, CallbackQuery):
            update.callback_query.answer()
            update.callback_query.edit_message_text(message)
        else:
            update.message.reply_text(message)
            
        return ADDING_WALLET
        
    except Exception as e:
        logger.error(f"Error in add_wallet_start: {e}")
        return ConversationHandler.END

def handle_wallet_address(update: Update, context: CallbackContext) -> int:
    try:
        wallet_address = update.message.text.strip()
        
        if not is_solana_wallet_address(wallet_address):
            update.message.reply_text(
                "That doesn't look like a valid Solana address. Please try again! 🤔\n"
                "Or send /cancel to cancel"
            )
            return ADDING_WALLET
            
        context.user_data['temp_wallet'] = wallet_address
        
        if not premium_manager.is_premium(str(update.effective_user.id)):
            context.user_data['temp_name'] = f"Wallet {wallet_address[:4]}...{wallet_address[-4:]}"
            return handle_wallet_name(update, context)
            
        update.message.reply_text(
            "Great! Now please send me a name for this wallet (e.g., 'Trading' or 'NFT Wallet') 📝\n"
            "Or send /cancel to cancel"
        )
        return ADDING_NAME
        
    except Exception as e:
        logger.error(f"Error handling wallet address: {e}")
        update.message.reply_text("Sorry, something went wrong. Please try again!")
        return ConversationHandler.END

def handle_wallet_name(update: Update, context: CallbackContext) -> int:
    try:
        user_id = str(update.effective_user.id)
        
        if premium_manager.is_premium(user_id):
            wallet_name = update.message.text.strip()
            if len(wallet_name) > 32:
                update.message.reply_text(
                    "Wallet name is too long! Please use max 32 characters.\n"
                    "Try again or send /cancel to cancel"
                )
                return ADDING_NAME
        else:
            wallet_name = context.user_data.get('temp_name')

        wallet_address = context.user_data.get('temp_wallet')
        if not wallet_address:
            update.message.reply_text("Something went wrong. Please start over with /add")
            return ConversationHandler.END

        success = db_manager.add_wallet(user_id, wallet_address, wallet_name)
        if success:
            try:
                success, webhook_id, addresses = get_webhook(HELIUS_WEBHOOK_ID)
                add_webhook(user_id, wallet_address, webhook_id, addresses)
            except Exception as e:
                logger.error(f"Error updating webhook: {e}")

            message = (
                f"✅ Successfully added wallet!\n\n"
                f"*Name:* {wallet_name}\n"
                f"*Address:* `{wallet_address[:4]}...{wallet_address[-4:]}`\n\n"
            )
            
            keyboard = [
                [
                    InlineKeyboardButton("➕ Add Another", callback_data="add_wallet"),
                    InlineKeyboardButton("👀 Show All", callback_data="show_wallets")
                ],
                [InlineKeyboardButton("🔙 Main Menu", callback_data="start")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            update.message.reply_text(
                message,
                reply_markup=reply_markup,
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            update.message.reply_text(
                "This wallet is already in your list! Try another one 🔄"
            )
        
        return ConversationHandler.END
        
    except Exception as e:
        logger.error(f"Error handling wallet name: {e}")
        update.message.reply_text("Sorry, something went wrong. Please try again!")
        return ConversationHandler.END

def main() -> None:
    try:
        # Verify essential environment variables
        required_vars = ['MONGODB_URI', 'BOT_TOKEN', 'HELIUS_KEY', 'HELIUS_WEBHOOK_ID']
        missing_vars = [var for var in required_vars if not os.environ.get(var)]
        if missing_vars:
            raise EnvironmentError(f"Missing required environment variables: {', '.join(missing_vars)}")

        # Initialize bot
        updater = Updater(BOT_TOKEN)
        dispatcher = updater.dispatcher

        # Add conversation handlers
        add_wallet_handler = ConversationHandler(
            entry_points=[
                CommandHandler('add', add_wallet_start),
                CallbackQueryHandler(add_wallet_start, pattern='^add_wallet$')
            ],
            states={
                ADDING_WALLET: [MessageHandler(Filters.text & ~Filters.command, handle_wallet_address)],
                ADDING_NAME: [MessageHandler(Filters.text & ~Filters.command, handle_wallet_name)],
            },
            fallbacks=[CommandHandler('cancel', cancel)]
        )

        delete_wallet_handler = ConversationHandler(
            entry_points=[
                CommandHandler('delete', delete_wallet_start),
                CallbackQueryHandler(delete_wallet_start, pattern='^delete_wallet$')
            ],
            states={
                DELETING_WALLET: [CallbackQueryHandler(handle_delete_callback, pattern='^delete_')],
            },
            fallbacks=[CallbackQueryHandler(start, pattern='^start$')]
        )

        # Add command handlers
        dispatcher.add_handler(CommandHandler("start", start))
        dispatcher.add_handler(add_wallet_handler)
        dispatcher.add_handler(delete_wallet_handler)
        dispatcher.add_handler(CommandHandler("show", show_wallets))

        # Add callback query handlers
        dispatcher.add_handler(CallbackQueryHandler(show_wallets, pattern='^show_wallets$'))
        dispatcher.add_handler(CallbackQueryHandler(start, pattern='^start$'))

        # Add error handler
        dispatcher.add_error_handler(error_handler)

        # Start the Bot
        logger.info("Starting bot...")
        updater.start_polling()
        logger.info("Bot started successfully!")
        
        # Run until Ctrl+C
        updater.idle()

    except Exception as e:
        logger.error(f"Critical error in main: {e}")
        raise

if __name__ == '__main__':
    main()
