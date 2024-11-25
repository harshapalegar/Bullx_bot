from datetime import datetime
import logging
from typing import Dict, Optional
from source.config import UserPlan, UserLimits

logger = logging.getLogger(__name__)

class DatabaseManager:
    def __init__(self, db):
        self.db = db

    def ensure_user_exists(self, user_id: str, username: Optional[str] = None) -> None:
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

    def get_user_stats(self, user_id: str) -> Dict:
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
                "wallet_limit": UserLimits.PREMIUM_WALLET_LIMIT if user_data.get("plan") == UserPlan.PREMIUM else UserLimits.FREE_WALLET_LIMIT,
                "joined_date": user_data.get("joined_date", datetime.now())
            }
        except Exception as e:
            logger.error(f"Error getting user stats: {e}")
            return {
                "active_wallets": 0,
                "transactions_today": 0,
                "plan": UserPlan.FREE,
                "wallet_limit": UserLimits.FREE_WALLET_LIMIT,
                "joined_date": datetime.now()
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
