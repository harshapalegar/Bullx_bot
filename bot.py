import logging
import os
from telegram import Update, ForceReply
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackQueryHandler
from telegram.ext import ConversationHandler
from pymongo import MongoClient
from datetime import datetime
from source.bot_tools import *

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
        update.callback_query.edit_message_text("The world is your oyster! Choose an action and let's embark on this thrilling journey! 🌍", reply_markup=reply_markup)

def next(update: Update, context: CallbackContext) -> None:
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

def back_button(update: Update, context: CallbackContext) -> None:
    keyboard = [
        [
            InlineKeyboardButton("🔙 Back", callback_data="back"),
        ]
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)
    return reply_markup

def button_callback(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    query.answer()

    if query.data == "addWallet":
        return add_wallet_start(update, context)
    elif query.data == "deleteWallet":
        return delete_wallet_start(update, context)
    elif query.data == "showWallets":
        return show_wallets(update, context)
    elif query.data == "back":
        return back(update, context)

def back(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    query.answer()
    query.edit_message_text("No worries! Let's head back to the main menu for more fun! 🎉")
    start(update, context)
    return ConversationHandler.END

def add_wallet_start(update: Update, context: CallbackContext) -> int:
    reply_markup = back_button(update, context)
    query = update.callback_query
    query.answer()
    query.edit_message_text("Alright, ready to expand your wallet empire? Send me the wallet address you'd like to add! 🎩", reply_markup=reply_markup)
    return ADDING_WALLET

def add_wallet_finish(update: Update, context: CallbackContext) -> int:
    reply_markup = back_button(update, context)
    wallet_address = update.message.text
    user_id = update.effective_user.id

    if not wallet_address:
        update.message.reply_text("Oops! Looks like you forgot the wallet address. Send it over so we can get things rolling! 📨", reply_markup=reply_markup)
        return

    if not is_solana_wallet_address(wallet_address):
        update.message.reply_text("Uh-oh! That Solana wallet address seems a bit fishy. Double-check it and send a valid one, please! 🕵️‍♂️", reply_markup=reply_markup)
        return
    
    check_res, check_num_tx = check_wallet_transactions(wallet_address)
    if not check_res:
        update.message.reply_text(f"Whoa, slow down Speedy Gonzales! 🏎️ We can only handle wallets with under 50 transactions per day. Your wallet's at {round(check_num_tx, 1)}. Let's pick another, shall we? 😉", reply_markup=reply_markup)
        return

    if wallet_count_for_user(user_id) >= 5:
        update.message.reply_text("Oops! You've reached the wallet limit! It seems you're quite the collector, but we can only handle up to 5 wallets per user. Time to make some tough choices! 😄", reply_markup=reply_markup)
        return

    existing_wallet = wallets_collection.find_one(
        {
            "user_id": str(user_id),
            "address": wallet_address,
            "status": "active"
        })

    if existing_wallet:
        update.message.reply_text("Hey there, déjà vu! You've already added this wallet. Time for a different action, perhaps? 🔄", reply_markup=reply_markup)
    else:
        reply_markup = next(update, context)
        success, webhook_id, addresses =
