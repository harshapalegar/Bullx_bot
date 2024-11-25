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

# Set up logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# Add HTTP health check handler
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

# Start health check server in a separate thread
health_check_thread = threading.Thread(target=run_health_check_server, daemon=True)
health_check_thread.start()

# Environment variables
MONGODB_URI = os.environ.get('MONGODB_URI')
BOT_TOKEN = os.environ.get('BOT_TOKEN')
TOKEN = BOT_TOKEN
HELIUS_KEY = os.environ.get('HELIUS_KEY')
HELIUS_WEBHOOK_ID = os.environ.get('HELIUS_WEBHOOK_ID')

ADDING_WALLET, DELETING_WALLET = range(2)

# MongoDB setup with error handling
try:
    client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=5000)
    client.admin.command('ping')
    db = client.sol_wallets
    wallets_collection = db.wallets
    logger.info("Successfully connected to MongoDB")
    
    # List all collections and documents count
    db_stats = {}
    for collection_name in db.list_collection_names():
        count = db[collection_name].count_documents({})
        db_stats[collection_name] = count
    logger.info(f"Database statistics: {db_stats}")
    
except Exception as e:
    logger.error(f"Failed to connect to MongoDB: {e}")
    raise

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
        logger.info(f"Checking transactions for address: {address}")
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
        
        logger.info(f"Helius API response status: {response.status_code}")
        if response.status_code != 200:
            logger.error(f"Helius API error: {response.status_code}, Response: {response.text}")
            return True, 0  # Allow adding wallet despite API error
            
        transactions = response.json()
        num_transactions = len(transactions)
        logger.info(f"Number of transactions for {address}: {num_transactions}")
        
        return True, num_transactions  # Removed transaction limit check
        
    except requests.exceptions.Timeout:
        logger.error(f"Timeout checking transactions for {address}")
        return True, 0
    except Exception as e:
        logger.error(f"Error checking transactions for {address}: {e}")
        return True, 0

def wallet_count_for_user(user_id):
    try:
        count = wallets_collection.count_documents({
            "user_id": str(user_id),
            "status": "active"
        })
        logger.info(f"User {user_id} has {count} active wallets")
        return count
    except Exception as e:
        logger.error(f"Error counting wallets for user {user_id}: {e}")
        return 0

def get_webhook(webhook_id):
    try:
        url = f"https://api.helius.xyz/v0/webhooks?api-key={HELIUS_KEY}"
        response = requests.get(url, timeout=10)
        
        if response.status_code != 200:
            logger.error(f"Helius API error: {response.status_code}")
            return False, None, []
            
        webhooks = response.json()
        logger.info(f"Found webhooks: {webhooks}")
        for webhook in webhooks:
            if webhook['webhookID'] == webhook_id:
                logger.info(f"Found matching webhook: {webhook}")
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
            logger.info(f"Address {address} already in webhook")
            return True
            
        url = f"https://api.helius.xyz/v0/webhooks/{webhook_id}?api-key={HELIUS_KEY}"
        
        webhook_url = os.environ.get('WEBHOOK_URL', f'https://your-render-url/wallet')
        
        new_addresses = existing_addresses + [address]
        data = {
            "accountAddresses": new_addresses,
            "webhookURL": webhook_url
        }
        
        logger.info(f"Updating webhook with data: {data}")
        response = requests.put(url, json=data, timeout=10)
        
        if response.status_code != 200:
            logger.error(f"Failed to update webhook: {response.status_code}, Response: {response.text}")
        return response.status_code == 200
        
    except requests.exceptions.Timeout:
        logger.error(f"Timeout adding webhook for address {address}")
        return False
    except Exception as e:
        logger.error(f"Error adding webhook for address {address}: {e}")
        return False

def delete_webhook(user_id, address, webhook_id, existing_addresses):
    try:
        if address not in existing_addresses:
            logger.info(f"Address {address} not in webhook")
            return True
            
        url = f"https://api.helius.xyz/v0/webhooks/{webhook_id}?api-key={HELIUS_KEY}"
        
        webhook_url = os.environ.get('WEBHOOK_URL', f'https://your-render-url/wallet')
        
        new_addresses = [addr for addr in existing_addresses if addr != address]
        data = {
            "accountAddresses": new_addresses,
            "webhookURL": webhook_url
        }
        
        logger.info(f"Updating webhook with data: {data}")
        response = requests.put(url, json=data, timeout=10)
        
        if response.status_code != 200:
            logger.error(f"Failed to update webhook: {response.status_code}, Response: {response.text}")
        return response.status_code == 200
        
    except requests.exceptions.Timeout:
        logger.error(f"Timeout deleting webhook for address {address}")
        return False
    except Exception as e:
        logger.error(f"Error deleting webhook for address {address}: {e}")
        return False

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
        "🤖 Ahoy there, Solana Wallet Wrangler! Welcome to Solana Wallet Xray Bot! 🤖\n\n"
        "I'm your trusty sidekick, here to help you juggle those wallets and keep an eye on transactions.\n"
        "Once you've added your wallets, you can sit back and relax, as I'll swoop in with a snappy notification "
        "and a brief transaction summary every time your wallet makes a move on Solana. 🚀\n"
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

def button_callback(update: Update, context: CallbackContext) -> None:
    try:
        query = update.callback_query
        query.answer()
        logger.info(f"Button callback received: {query.data}")

        if query.data == "addWallet":
            return add_wallet_start(update, context)
        elif query.data == "deleteWallet":
            return delete_wallet_start(update, context)
        elif query.data == "showWallets":
            return show_wallets(update, context)
        elif query.data == "back":
            return back(update, context)
    except Exception as e:
        logger.error(f"Error in button_callback: {e}")
        return ConversationHandler.END

def back(update: Update, context: CallbackContext) -> int:
    try:
        query = update.callback_query
        query.answer()
        query.edit_message_text("No worries! Let's head back to the main menu for more fun! 🎉")
        start(update, context)
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"Error in back: {e}")
        return ConversationHandler.END

def add_wallet_start(update: Update, context: CallbackContext) -> int:
    try:
        reply_markup = back_button(update, context)
        query = update.callback_query
        query.answer()
        query.edit_message_text(
            "Alright, ready to expand your wallet empire? Send me the wallet address you'd like to add! 🎩",
            reply_markup=reply_markup
        )
        return ADDING_WALLET
    except Exception as e:
        logger.error(f"Error in add_wallet_start: {e}")
        return ConversationHandler.END

def add_wallet_finish(update: Update, context: CallbackContext) -> int:
    try:
        reply_markup = back_button(update, context)
        wallet_address = update.message.text
        user_id = update.effective_user.id
        
        logger.info(f"Processing wallet addition for user {user_id}, address: {wallet_address}")

        if not wallet_address:
            update.message.reply_text(
                "Oops! Looks like you forgot the wallet address. Send it over so we can get things rolling! 📨",
                reply_markup=reply_markup
            )
            return ADDING_WALLET

        if not is_solana_wallet_address(wallet_address):
            update.message.reply_text(
                "Uh-oh! That Solana wallet address seems a bit fishy. Double-check it and send a valid one, please! 🕵️‍♂️",
                reply_markup=reply_markup
            )
            return ADDING_WALLET

        wallet_count = wallet_count_for_user(user_id)
        logger.info(f"User {user_id} current wallet count: {wallet_count}")
        
        if wallet_count >= 5:
            update.message.reply_text(
                "Oops! You've reached the wallet limit! It seems you're quite the collector, but we can only handle "
                "up to 5 wallets per user. Time to make some tough choices! 😄",
                reply_markup=reply_markup
            )
            return ADDING_WALLET

        existing_wallet = wallets_collection.find_one(
            {
                "user_id": str(user_id),
                "address": wallet_address,
                "status": "active"
            })
        
        logger.info(f"Existing wallet check result: {existing_wallet}")

        if existing_wallet:
            update.message.reply_text(
                "Hey there, déjà vu! You've already added this wallet. Time for a different action, perhaps? 🔄",
                reply_markup=reply_markup
            )
        else:
            reply_markup = next(update, context)
            success, webhook_id, addresses = get_webhook(HELIUS_WEBHOOK_ID)
            logger.info(f"Webhook check result - success: {success}, webhook_id: {webhook_id}, addresses: {addresses}")
            
            r_success = add_webhook(user_id, wallet_address, webhook_id, addresses)
            logger.info(f"Add webhook result: {r_success}")
            
            if (success) and (r_success):
                main = {
                    "user_id": str(user_id),
                    "address": wallet_address,
                    "datetime": datetime.now(),
                    "status": 'active',
                }
                result = wallets_collection.insert_one(main)
                logger.info(f"Wallet added to database with ID: {result.inserted_id}")
                
                update.message.reply_text(
                    "Huzzah! Your wallet has been added with a flourish! 🎉 Now you can sit back, relax, and enjoy "
                    "your Solana experience as I keep an eye on your transactions. What's your next grand plan?",
                    reply_markup=reply_markup
                )
            else:
                update.message.reply_text(
                    "Bummer! We hit a snag while saving your wallet. Let's give it another whirl, shall we? 🔄",
                    reply_markup=reply_markup
                )

        return ConversationHandler.END
    except Exception as e:
        logger.error(f"Error in add_wallet_finish: {e}")
        return ConversationHandler.END

def delete_wallet_start(update: Update, context: CallbackContext) -> int:
    try:
        reply_markup = back_button(update, context)
        query = update.callback_query
        query.answer()
        query.edit_message_text(
            "Time for some spring cleaning? Send the wallet address you'd like to sweep away! 🧹",
            reply_markup=reply_markup
        )
        return DELETING_WALLET
    except Exception as e:
        logger.error(f"Error in delete_wallet_start: {e}")
        return ConversationHandler.END

def delete_wallet_finish(update: Update, context: CallbackContext) -> int:
    try:
        wallet_address = update.message.text
        user_id = update.effective_user.id
        
        logger.info(f"Processing wallet deletion for user {user_id}, address: {wallet_address}")

        if not is_solana_wallet_address(wallet_address):
            update.message.reply_text(
                "Hmm, that doesn't look like a valid Solana address. Want to try again? 🤔",
                reply_markup=back_button(update, context)
            )
            return DELETING_WALLET

        wallets_exist = list(wallets_collection.find(
            {
                "address": wallet_address,
                "status": "active"
            }))
        
        logger.info(f"Found existing wallets: {wallets_exist}")
        
        r_success = True
        if len(wallets_exist) == 1:
            logger.info('Deleting unique address from webhook')
            success, webhook_id, addresses = get_webhook(HELIUS_WEBHOOK_ID)
            r_success = delete_webhook(user_id, wallet_address, webhook_id, addresses)
            logger.info(f"Delete webhook result: {r_success}")
        else:
            logger.info('Address not unique, not deleting from webhook')

        reply_markup = back_button(update, context)
        if r_success:
            result = wallets_collection.delete_one({
                "user_id": str(user_id), 
                "address": wallet_address,
                "status": "active"
            })
            
            logger.info(f"Delete result: {result.deleted_count}")
            
            if result.deleted_count == 0:
                update.message.reply_text(
                    "Hmm, that wallet's either missing or not yours. Let's try something else, okay? 🕵️‍♀️",
                    reply_markup=reply_markup
                )
            else:
                update.message.reply_text(
                    "Poof! Your wallet has vanished into thin air! Now, what other adventures await? ✨",
                    reply_markup=reply_markup
                )
        else:
            update.message.reply_text(
                "Yikes, we couldn't delete the wallet. Don't worry, we'll get it next time! Try again, please. 🔄",
                reply_markup=reply_markup
            )

        return ConversationHandler.END
    except Exception as e:
        logger.error(f"Error in delete_wallet_finish: {e}")
        return ConversationHandler.END

def show_wallets(update: Update, context: CallbackContext) -> None:
    try:
        reply_markup = next(update, context)
        user_id = update.effective_user.id
        
        logger.info(f"Fetching wallets for user {user_id}")

        user_wallets = list(wallets_collection.find(
            {
                "user_id": str(user_id),
                "status": "active"
            }))
        
        logger.info(f"Found wallets: {user_wallets}")
        
        if len(user_wallets) == 0:
            update.callback_query.answer()
            update.callback_query.edit_message_text(
                "Whoa, no wallets here! Let's add some, or pick another action to make things exciting! 🎢",
                reply_markup=reply_markup
            )
        else:
            wallet_list = "\n".join([wallet["address"] for wallet in user_wallets])
            update.callback_query.answer()
            update.callback_query.edit_message_text(
                f"Feast your eyes upon your wallet collection! 🎩\n\n{wallet_list}\n\nNow, what's your next move, my friend? 🤔",
                reply_markup=reply_markup
            )
    except Exception as e:
        logger.error(f"Error in show_wallets: {e}")
        try:
            update.callback_query.edit_message_text(
                "Sorry, something went wrong while fetching your wallets. Please try again! 🔄",
                reply_markup=next(update, context)
            )
        except:
            pass

def main() -> None:
    try:
        # Verify essential environment variables
        required_vars = ['MONGODB_URI', 'BOT_TOKEN', 'HELIUS_KEY', 'HELIUS_WEBHOOK_ID']
        missing_vars = [var for var in required_vars if not os.environ.get(var)]
        if missing_vars:
            raise EnvironmentError(f"Missing required environment variables: {', '.join(missing_vars)}")

        # List all environment variables (without values)
        logger.info(f"Environment variables present: {[key for key in os.environ.keys()]}")

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
