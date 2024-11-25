from datetime import datetime
import logging
from source.config import UserPlan, PREMIUM_FEATURES, FREE_FEATURES

logger = logging.getLogger(__name__)

class PremiumManager:
    def __init__(self, db, db_manager: DatabaseManager):
        self.db = db
        self.db_manager = db_manager

    def is_premium(self, user_id: str) -> bool:
        try:
            user = self.db.users.find_one({"user_id": str(user_id)})
            return user and user.get("plan") == UserPlan.PREMIUM
        except Exception as e:
            logger.error(f"Error checking premium status: {e}")
            return False

    def get_user_features(self, user_id: str) -> dict:
        try:
            is_premium = self.is_premium(user_id)
            return PREMIUM_FEATURES if is_premium else FREE_FEATURES
        except Exception as e:
            logger.error(f"Error getting user features: {e}")
            return FREE_FEATURES

    def format_premium_message(self) -> str:
        message = (
            "*🌟 Premium Features*\n\n"
            "Upgrade to Premium and get:\n"
            f"• Monitor up to {PREMIUM_FEATURES['wallet_limit']} wallets\n"
            "• Custom wallet names\n"
            "• Priority notifications\n"
            "• Detailed transaction history\n"
            "• Advanced analytics\n\n"
            "Contact @admin to upgrade!"
        )
        return message
