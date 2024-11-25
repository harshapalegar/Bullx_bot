import logging
import os
import requests
import base58
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import Update, ForceReply, Bot, ParseMode
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackQueryHandler
from telegram.ext import ConversationHandler
from pymongo import MongoClient
from datetime import datetime, timedelta
from telegram.error import BadRequest, Unauthorized, TimedOut

# Import configurations and utilities
from source.config import (
    BOT_TOKEN, MONGODB_URI, HELIUS_KEY, HELIUS_WEBHOOK_ID, 
    UserPlan, UserLimits
)
from source.utils.admin_utils import AdminSystem
from source.utils.database_utils import DatabaseManager
from source.utils.premium_utils import PremiumManager

# States for conversation handler
ADDING_WALLET, ADDING_NAME, DELETING_WALLET = range(3)

# Set up logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# Health check server
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

# Initialize MongoDB and utilities
try:
    client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=5000)
    client.admin.command('ping')
    db = client.sol_wallets
    logger.info("Successfully connected to MongoDB")

    # Initialize utility managers
    admin_system = AdminSystem(db)
    db_manager = DatabaseManager(db)
    premium_manager = PremiumManager(db)
    
except Exception as e:
    logger.error(f"Failed to connect to MongoDB: {e}")
    raise

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

def add_wallet_start(update: Update, context: CallbackContext) -> int:
    try:
        user_id = str(update.effective_user.id)
        
        # Check wallet limit
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
        user_id = str(update.effective_user.id)
        wallet_address = update.message.text.strip()

        if not is_solana_wallet_address(wallet_address):
            update.message.reply_text(
                "That doesn't look like a valid Solana address. Please try again! 🤔\n"
                "Or send /cancel to cancel"
            )
            return ADDING_WALLET

        # Store address temporarily
        context.user_data['temp_wallet'] = wallet_address
        
        # For free users, use address as name
        if not premium_manager.is_premium(user_id):
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
        
        # Get wallet name (either from message or from context for free users)
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

        # Add wallet to database
        success = db_manager.add_wallet(user_id, wallet_address, wallet_name)
        if not success:
            update.message.reply_text(
                "This wallet is already in your list! Try another one 🔄"
            )
            return ConversationHandler.END

        # Update Helius webhook
        try:
            success, webhook_id, addresses = get_webhook(HELIUS_WEBHOOK_ID)
            add_webhook(user_id, wallet_address, webhook_id, addresses)
        except Exception as e:
            logger.error(f"Error updating webhook: {e}")

        # Successful addition message
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
        return ConversationHandler.END
        
    except Exception as e:
        logger.error(f"Error handling wallet name: {e}")
        update.message.reply_text("Sorry, something went wrong. Please try again!")
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
            update.message.reply_text("Sorry, something went wrong. Please try again!")
        except:
            update.callback_query.edit_message_text("Sorry, something went wrong. Please try again!")

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

def handle_delete_callback(update: Update, context: CallbackContext) -> int:
    try:
        query = update.callback_query
        user_id = str(update.effective_user.id)
        
        if query.data == "start":
            start(update, context)
            return ConversationHandler.END
            
        wallet_name = query.data.replace("delete_", "")
        success = db_manager.delete_wallet(user_id, wallet_name)
        
        if success:
            message = f"Successfully deleted wallet '*{wallet_name}*'! 🗑️"
        else:
            message = "Couldn't find that wallet. It might have been already deleted! 🤔"
            
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
        return ConversationHandler.END

# Admin Commands
def admin_panel(update: Update, context: CallbackContext) -> None:
    try:
        user_id = str(update.effective_user.id)
        if not admin_system.is_admin(user_id):
            update.message.reply_text("⚠️ Access denied")
            return

        stats = admin_system.get_system_stats()
        
        message = (
            "*🔐 Admin Panel*\n\n"
            f"*System Statistics:*\n"
            f"Total Users: `{stats['total_users']}`\n"
            f"Total Wallets: `{stats['total_wallets']}`\n"
            f"Premium Users: `{stats['premium_users']}`\n"
            f"Active Today: `{stats['active_users_today']}`\n"
            f"Today's Transactions: `{stats['transactions_today']}`\n\n"
            "*Admin Commands:*\n"
            "/admin_users - List all users\n"
            "/admin_stats - Detailed statistics\n"
            "/broadcast - Send message to all users\n"
            "/add_premium <user_id> - Add premium user\n"
        )

        keyboard = [[InlineKeyboardButton("🔙 Main Menu", callback_data="start")]]
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
        logger.error(f"Error in admin panel: {e}")
        update.message.reply_text("Sorry, something went wrong!")

def admin_users(update: Update, context: CallbackContext) -> None:
    try:
        user_id = str(update.effective_user.id)
        if not admin_system.is_admin(user_id):
            update.message.reply_text("⚠️ Access denied")
            return

        users = admin_system.get_user_list()
        
        messages = ["*👥 User List*\n"]
        current_message = ""
        
        for user in users:
            user_info = (
                f"\n*User ID:* `{user['user_id']}`\n"
                f"Username: @{user.get('username', 'None')}\n"
                f"Plan: `{user['plan']}`\n"
                f"Wallets: `{user['wallets']}`\n"
                f"Today's Transactions: `{user['transactions_today']}`\n"
                f"Joined: `{user['joined_date'].strftime('%Y-%m-%d')}`\n"
                "───────────────\n"
            )
            
            if len(current_message + user_info) > 4000:
                messages.append(current_message)
                current_message = user_info
            else:
                current_message += user_info
        
        if current_message:
            messages.append(current_message)
        
        # Send messages
        for idx, message in enumerate(messages):
            if idx == len(messages) - 1:
                keyboard = [[InlineKeyboardButton("🔙 Admin Panel", callback_data="admin")]]
                reply_markup = InlineKeyboardMarkup(keyboard)
                update.message.reply_text(
                    message,
                    reply_markup=reply_markup,
                    parse_mode=ParseMode.MARKDOWN
                )
            else:
                update.message.reply_text(
                    message,
                    parse_mode=ParseMode.MARKDOWN
                )

    except Exception as e:
        logger.error(f"Error in admin users command: {e}")
        update.message.reply_text("Sorry, something went wrong!")

def broadcast(update: Update, context: CallbackContext) -> None:
    try:
        user_id = str(update.effective_user.id)
        if not admin_system.is_admin(user_id):
            update.message.reply_text("⚠️ Access denied")
            return

        if not context.args:
            update.message.reply_text(
                "Usage: /broadcast <message>\n"
                "Use HTML formatting for styling."
            )
            return

        broadcast_message = " ".join(context.args)
        results = admin_system.broadcast_message(broadcast_message)

        update.message.reply_text(
            f"Broadcast completed!\n"
            f"Success: {results['success']}\n"
            f"Failed: {results['failed']}"
        )

    except Exception as e:
        logger.error(f"Error in broadcast command: {e}")
        update.message.reply_text("Sorry, something went wrong!")

def add_premium(update: Update, context: CallbackContext) -> None:
    try:
        user_id = str(update.effective_user.id)
        if not admin_system.is_admin(user_id):
            update.message.reply_text("⚠️ Access denied")
            return

        if not context.args:
            update.message.reply_text("Usage: /add_premium <user_id>")
            return

        target_user_id = context.args[0]
        success = premium_manager.upgrade_to_premium(target_user_id)

        if success:
            update.message.reply_text(f"Successfully upgraded user {target_user_id} to Premium! ⭐️")
        else:
            update.message.reply_text("Failed to upgrade user. Please check the user ID.")

    except Exception as e:
        logger.error(f"Error in add premium command: {e}")
        update.message.reply_text("Sorry, something went wrong!")

# Premium Features
def premium_command(update: Update, context: CallbackContext) -> None:
    try:
        user_id = str(update.effective_user.id)
        is_premium = premium_manager.is_premium(user_id)
        
        if is_premium:
            message = (
                "*⭐️ Premium Status*\n\n"
                "You are a Premium user!\n\n"
                "*Your Benefits:*\n"
                f"• Up to {UserLimits.PREMIUM_WALLET_LIMIT} wallets\n"
                "• Custom wallet names\n"
                "• Priority notifications\n"
                "• Detailed transaction history\n"
                "• Advanced analytics\n\n"
                "Thank you for your support! 🙏"
            )
        else:
            message = premium_manager.format_premium_message()

        keyboard = [[InlineKeyboardButton("🔙 Main Menu", callback_data="start")]]
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
        logger.error(f"Error in premium command: {e}")
        update.message.reply_text("Sorry, something went wrong!")

def stats_command(update: Update, context: CallbackContext) -> None:
    try:
        user_id = str(update.effective_user.id)
        stats = db_manager.get_user_stats(user_id)
        is_premium = premium_manager.is_premium(user_id)

        if admin_system.is_admin(user_id):
            admin_stats = admin_system.get_system_stats()
            message = (
                "*📊 Admin Statistics*\n\n"
                f"Total Users: `{admin_stats['total_users']}`\n"
                f"Total Wallets: `{admin_stats['total_wallets']}`\n"
                f"Premium Users: `{admin_stats['premium_users']}`\n"
                f"Active Today: `{admin_stats['active_users_today']}`\n\n"
                "*Your Statistics:*\n"
                f"Active Wallets: `{stats['active_wallets']}/{stats['wallet_limit']}`\n"
                f"Transactions Today: `{stats['transactions_today']}`\n"
                f"Plan: `{stats['plan'].title()}`"
            )
        else:
            message = (
                "*📊 Your Statistics*\n\n"
                f"Active Wallets: `{stats['active_wallets']}/{stats['wallet_limit']}`\n"
                f"Transactions Today: `{stats['transactions_today']}`\n"
                f"Plan: `{stats['plan'].title()}`\n\n"
            )
            if not is_premium:
                message += (
                    "*Want more features? Get Premium!*\n"
                    f"• Increase limit to {UserLimits.PREMIUM_WALLET_LIMIT} wallets\n"
                    "• Get priority notifications\n"
                    "Use /premium to learn more!"
                )

        keyboard = [[InlineKeyboardButton("🔙 Main Menu", callback_data="start")]]
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
        logger.error(f"Error in stats command: {e}")
        update.message.reply_text("Sorry, something went wrong!")

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
        dispatcher.add_handler(CommandHandler("stats", stats_command))
        dispatcher.add_handler(CommandHandler("premium", premium_command))

        # Admin commands
        dispatcher.add_handler(CommandHandler("admin", admin_panel))
        dispatcher.add_handler(CommandHandler("admin_users", admin_users))
        dispatcher.add_handler(CommandHandler("broadcast", broadcast))
        dispatcher.add_handler(CommandHandler("add_premium", add_premium))

        # Callback query handlers
        dispatcher.add_handler(CallbackQueryHandler(show_wallets, pattern='^show_wallets$'))
        dispatcher.add_handler(CallbackQueryHandler(stats_command, pattern='^stats$'))
        dispatcher.add_handler(CallbackQueryHandler(premium_command, pattern='^premium$'))
        dispatcher.add_handler(CallbackQueryHandler(admin_panel, pattern='^admin$'))
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
