import logging
import os
import requests
import base58
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import Update, ForceReply, Bot
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackQueryHandler
from telegram.ext import ConversationHandler
from pymongo import MongoClient
from datetime import datetime, timedelta
from telegram.error import BadRequest, Unauthorized, TimedOut

# Add HTTP health check handler
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health" or self.path == "/":
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(bytes('{"status":"healthy"}', "utf-8"))
        else:
            self.send_response(404)
            self.end_headers()
        
    def log_message(self, format, *args):
        # Suppress logging of health check requests
        pass

def run_health_check_server():
    try:
        server_address = ('', 8000)
        httpd = HTTPServer(server_address, HealthCheckHandler)
        logger.info("Health check server started on port 8000")
        httpd.serve_forever()
    except Exception as e:
        logger.error(f"Error in health check server: {e}")

# Environment variables
MONGODB_URI = os.environ.get('MONGODB_URI')
BOT_TOKEN = os.environ.get('BOT_TOKEN')
TOKEN = BOT_TOKEN
HELIUS_KEY = os.environ.get('HELIUS_KEY')
HELIUS_WEBHOOK_ID = os.environ.get('HELIUS_WEBHOOK_ID')

ADDING_WALLET, DELETING_WALLET = range(2)

try:
    client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=5000)
    # Verify database connection
    client.server_info()
    db = client.sol_wallets
    wallets_collection = db.wallets
    logger.info("Successfully connected to MongoDB")
except Exception as e:
    logger.error(f"Failed to connect to MongoDB: {e}")
    raise

# Set up logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# Start health check server in a separate thread
health_check_thread = threading.Thread(target=run_health_check_server, daemon=True)
health_check_thread.start()

# Utility functions
def is_solana_wallet_address(address):
    if not address:
        return False
    try:
        decoded = base58.b58decode(address)
        return len(decoded) == 32
    except Exception as e:
        logger.error(f"Error validating Solana address: {e}")
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
        response = requests.get(url, params=params, timeout=10)
        
        if response.status_code != 200:
            logger.error(f"Helius API error: {response.status_code}")
            return False, 0
            
        transactions = response.json()
        num_transactions = len(transactions)
        
        return num_transactions < 50, num_transactions
        
    except requests.exceptions.Timeout:
        logger.error("Timeout checking wallet transactions")
        return False, 0
    except Exception as e:
        logger.error(f"Error checking wallet transactions: {e}")
        return False, 0

def wallet_count_for_user(user_id):
    try:
        return wallets_collection.count_documents({
            "user_id": str(user_id),
            "status": "active"
        })
    except Exception as e:
        logger.error(f"Error counting user wallets: {e}")
        return 0

def get_webhook(webhook_id):
    try:
        url = f"https://api.helius.xyz/v0/webhooks?api-key={HELIUS_KEY}"
        response = requests.get(url, timeout=10)
        
        if response.status_code != 200:
            logger.error(f"Helius API error: {response.status_code}")
            return False, None, []
            
        webhooks = response.json()
        for webhook in webhooks:
            if webhook['webhookID'] == webhook_id:
                return True, webhook_id, webhook.get('accountAddresses', [])
                
        return False, None, []
        
    except requests.exceptions.Timeout:
        logger.error("Timeout getting webhook")
        return False, None, []
    except Exception as e:
        logger.error(f"Error getting webhook: {e}")
        return False, None, []

def add_webhook(user_id, address, webhook_id, existing_addresses):
    try:
        if address in existing_addresses:
            return True
            
        url = f"https://api.helius.xyz/v0/webhooks/{webhook_id}?api-key={HELIUS_KEY}"
        
        webhook_url = os.environ.get('WEBHOOK_URL')
        if not webhook_url:
            logger.error("WEBHOOK_URL environment variable not set")
            return False

        new_addresses = existing_addresses + [address]
        data = {
            "accountAddresses": new_addresses,
            "webhookURL": webhook_url
        }
        
        response = requests.put(url, json=data, timeout=10)
        if response.status_code != 200:
            logger.error(f"Failed to add webhook: {response.status_code}")
        return response.status_code == 200
        
    except requests.exceptions.Timeout:
        logger.error("Timeout adding webhook")
        return False
    except Exception as e:
        logger.error(f"Error adding webhook: {e}")
        return False

def delete_webhook(user_id, address, webhook_id, existing_addresses):
    try:
        if address not in existing_addresses:
            return True
            
        url = f"https://api.helius.xyz/v0/webhooks/{webhook_id}?api-key={HELIUS_KEY}"
        
        webhook_url = os.environ.get('WEBHOOK_URL')
        if not webhook_url:
            logger.error("WEBHOOK_URL environment variable not set")
            return False

        new_addresses = [addr for addr in existing_addresses if addr != address]
        data = {
            "accountAddresses": new_addresses,
            "webhookURL": webhook_url
        }
        
        response = requests.put(url, json=data, timeout=10)
        if response.status_code != 200:
            logger.error(f"Failed to delete webhook: {response.status_code}")
        return response.status_code == 200
        
    except requests.exceptions.Timeout:
        logger.error("Timeout deleting webhook")
        return False
    except Exception as e:
        logger.error(f"Error deleting webhook: {e}")
        return False

# Message handlers
def error_handler(update: Update, context: CallbackContext) -> None:
    logger.error(f"Update {update} caused error {context.error}")
    try:
        if isinstance(context.error, (BadRequest, Unauthorized)):
            # Handle Telegram API errors
            return
        if update.message:
            update.message.reply_text(
                "Sorry, something went wrong. Please try again later! 🔧"
            )
    except Exception as e:
        logger.error(f"Error in error handler: {e}")

def welcome_message() -> str:
    message = (
        "🤖 Ahoy there, Solana Wallet Wrangler! Welcome to Solana Wallet Xray Bot! 🤖\n\n"
        "I'm your trusty sidekick, here to help you juggle those wallets and keep an eye on transactions.\n"
        "Once you've added your wallets, you can sit back and relax, as I'll swoop in with a snappy notification and a brief transaction summary every time your wallet makes a move on Solana. 🚀\n"
        "Have a blast using the bot! 😄\n\n"
        "Ready to rumble? Use the commands below and follow the prompts:"
    )
    return message

def start(update: Update, context: CallbackContext) -> None:
    try:
        keyboard = [
            [
                InlineKeyboardButton("✨ Add", callback_data="addWallet"),
                InlineKeyboardButton("🗑️ Delete", callback_data="deleteWallet"),
                InlineKeyboardButton("👀 Show", callback_data="showWallets"),
            ]
        ]

        reply_markup = InlineKeyboardMarkup(keyboard)

        if update.message:
            update.message.reply_text(welcome_message(), reply_markup=reply_markup)
        else:
            update.callback_query.edit_message_text(
                "The world is your oyster! Choose an action and let's embark on this thrilling journey! 🌍",
                reply_markup=reply_markup
            )
    except Exception as e:
        logger.error(f"Error in start command: {e}")
        if update.message:
            update.message.reply_text("Sorry, something went wrong. Please try /start again!")

def next(update: Update, context: CallbackContext) -> None:
    try:
        keyboard = [
            [
                InlineKeyboardButton("✨ Add", callback_data="addWallet"),
                InlineKeyboardButton("🗑️ Delete", callback_data="deleteWallet"),
                InlineKeyboardButton("👀 Show", callback_data="showWallets"),
            ],
            [
                InlineKeyboardButton("🔙 Back", callback_data="back"),
            ]
        ]

        reply_markup = InlineKeyboardMarkup(keyboard)
        return reply_markup
    except Exception as e:
        logger.error(f"Error in next: {e}")
        return None

def back_button(update: Update, context: CallbackContext) -> None:
    try:
        keyboard = [
            [
                InlineKeyboardButton("🔙 Back", callback_data="back"),
            ]
        ]

        reply_markup = InlineKeyboardMarkup(keyboard)
        return reply_markup
    except Exception as e:
        logger.error(f"Error in back_button: {e}")
        return None

[... Rest of the handlers remain the same as in previous bot.py with added error handling ...]

def main() -> None:
    try:
        # Initialize bot
        updater = Updater(TOKEN)
        dispatcher = updater.dispatcher

        # Add handlers
        conv_handler = ConversationHandler(
            entry_points=[CallbackQueryHandler(button_callback)],
            states={
                ADDING_WALLET: [MessageHandler(Filters.text & ~Filters.command, add_wallet_finish)],
                DELETING_WALLET: [MessageHandler(Filters.text & ~Filters.command, delete_wallet_finish)],
            },
            fallbacks=[CallbackQueryHandler(back, pattern='^back$')],
        )

        dispatcher.add_handler(CommandHandler("start", start))
        dispatcher.add_handler(conv_handler)
        
        # Add error handler
        dispatcher.add_error_handler(error_handler)

        # Start the Bot
        logger.info("Starting bot...")
        updater.start_polling()
        
        # Run the bot until the user presses Ctrl-C
        updater.idle()

    except Exception as e:
        logger.error(f"Critical error in main: {e}")
        raise

if __name__ == '__main__':
    main()
