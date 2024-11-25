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
        update.callback_query.edit_message_text(
            "The world is your oyster! Choose an action and let's embark on this thrilling journey! 🌍",
            reply_markup=reply_markup
        )

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
    query.edit_message_text(
        "Alright, ready to expand your wallet empire? Send me the wallet address you'd like to add! 🎩",
        reply_markup=reply_markup
    )
    return ADDING_WALLET

def add_wallet_finish(update: Update, context: CallbackContext) -> int:
    reply_markup = back_button(update, context)
    wallet_address = update.message.text
    user_id = update.effective_user.id

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
    
    check_res, check_num_tx = check_wallet_transactions(wallet_address)
    if not check_res:
        update.message.reply_text(
            f"Whoa, slow down Speedy Gonzales! 🏎️ We can only handle wallets with under 50 transactions per day. Your wallet's at {round(check_num_tx, 1)}. Let's pick another, shall we? 😉",
            reply_markup=reply_markup
        )
        return ADDING_WALLET

    if wallet_count_for_user(user_id) >= 5:
        update.message.reply_text(
            "Oops! You've reached the wallet limit! It seems you're quite the collector, but we can only handle up to 5 wallets per user. Time to make some tough choices! 😄",
            reply_markup=reply_markup
        )
        return ADDING_WALLET

    existing_wallet = wallets_collection.find_one(
        {
            "user_id": str(user_id),
            "address": wallet_address,
            "status": "active"
        })

    if existing_wallet:
        update.message.reply_text(
            "Hey there, déjà vu! You've already added this wallet. Time for a different action, perhaps? 🔄",
            reply_markup=reply_markup
        )
    else:
        reply_markup = next(update, context)
        success, webhook_id, addresses = get_webhook(HELIUS_WEBHOOK_ID)
        r_success = add_webhook(user_id, wallet_address, webhook_id, addresses)
        
        if (success) and (r_success):
            main = {
                "user_id": str(user_id),
                "address": wallet_address,
                "datetime": datetime.now(),
                "status": 'active',
            }
            wallets_collection.insert_one(main)
                
            update.message.reply_text(
                "Huzzah! Your wallet has been added with a flourish! 🎉 Now you can sit back, relax, and enjoy your Solana experience as I keep an eye on your transactions. What's your next grand plan?",
                reply_markup=reply_markup
            )
        else:
            update.message.reply_text(
                "Bummer! We hit a snag while saving your wallet. Let's give it another whirl, shall we? 🔄",
                reply_markup=reply_markup
            )

    return ConversationHandler.END

def delete_wallet_start(update: Update, context: CallbackContext) -> int:
    reply_markup = back_button(update, context)
    query = update.callback_query
    query.answer()
    query.edit_message_text(
        "Time for some spring cleaning? Send the wallet address you'd like to sweep away! 🧹",
        reply_markup=reply_markup
    )
    return DELETING_WALLET

def delete_wallet_finish(update: Update, context: CallbackContext) -> int:
    reply_markup = next(update, context)
    wallet_address = update.message.text
    user_id = update.effective_user.id

    wallets_exist = wallets_collection.find(
        {
            "address": wallet_address,
            "status": "active"
        })
    r_success = True
    if len(list(wallets_exist)) == 1:
        logging.info('deleting unique address')
        success, webhook_id, addresses = get_webhook(HELIUS_WEBHOOK_ID)
        r_success = delete_webhook(user_id, wallet_address, webhook_id, addresses)
    else:
        logging.info('address not unique, not deleting')

    reply_markup = back_button(update, context)
    if r_success:
        result = wallets_collection.delete_one({"user_id": str(user_id), "address": wallet_address})
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

def show_wallets(update: Update, context: CallbackContext) -> None:
    reply_markup = next(update, context)
    user_id = update.effective_user.id

    user_wallets = list(wallets_collection.find(
        {
            "user_id": str(user_id),
            "status": "active"
        }))
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

def main() -> None:
    updater = Updater(TOKEN)
    dispatcher = updater.dispatcher

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

    updater.start_polling()
    updater.idle()

if __name__ == '__main__':
    main()
