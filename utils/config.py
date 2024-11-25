import os
from dotenv import load_dotenv

load_dotenv()

# Bot Configuration
BOT_TOKEN = os.getenv('BOT_TOKEN')
MONGODB_URI = os.getenv('MONGODB_URI')
HELIUS_KEY = os.getenv('HELIUS_KEY')
HELIUS_WEBHOOK_ID = os.getenv('HELIUS_WEBHOOK_ID')
WEBHOOK_URL = os.getenv('WEBHOOK_URL')

# Admin Configuration
ADMIN_IDS = ['1086229166']  # Replace with your Telegram ID

# User Plans
class UserPlan:
    FREE = "free"
    PREMIUM = "premium"

class UserLimits:
    FREE_WALLET_LIMIT = 3
    PREMIUM_WALLET_LIMIT = 10

# Premium Features
PREMIUM_FEATURES = {
    "wallet_limit": UserLimits.PREMIUM_WALLET_LIMIT
}

FREE_FEATURES = {
    "wallet_limit": UserLimits.FREE_WALLET_LIMIT
}
