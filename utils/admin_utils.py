from typing import Dict, List
from datetime import datetime
import logging
from source.config import ADMIN_IDS, UserPlan

logger = logging.getLogger(__name__)

class AdminSystem:
    def __init__(self, db):
        self.db = db
        self._admin_ids = ADMIN_IDS

    def is_admin(self, user_id: str) -> bool:
        return str(user_id) in self._admin_ids

    def get_system_stats(self) -> Dict:
        try:
            today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            
            stats = {
                "total_users": len(set(w["user_id"] for w in self.db.wallets.find({"status": "active"}))),
                "total_wallets": self.db.wallets.count_documents({"status": "active"}),
                "wallets_today": self.db.wallets.count_documents({
                    "datetime": {"$gte": today},
                    "status": "active"
                }),
                "active_users_today": len(set(w["user_id"] for w in self.db.wallets.find({
                    "datetime": {"$gte": today},
                    "status": "active"
                }))),
                "premium_users": self.db.users.count_documents({"plan": UserPlan.PREMIUM}),
                "total_transactions": self.db.messages.count_documents({}),
                "transactions_today": self.db.messages.count_documents({
                    "datetime": {"$gte": today}
                })
            }
            return stats
        except Exception as e:
            logger.error(f"Error getting system stats: {e}")
            return {
                "total_users": 0,
                "total_wallets": 0,
                "wallets_today": 0,
                "active_users_today": 0,
                "premium_users": 0,
                "total_transactions": 0,
                "transactions_today": 0
            }

    def get_user_list(self) -> List[Dict]:
        try:
            users = []
            for user in self.db.users.find({}):
                user_stats = {
                    "user_id": user["user_id"],
                    "username": user.get("username", "Unknown"),
                    "plan": user.get("plan", UserPlan.FREE),
                    "joined_date": user.get("joined_date", datetime.now()),
                    "wallets": self.db.wallets.count_documents({
                        "user_id": user["user_id"],
                        "status": "active"
                    }),
                    "transactions_today": self.db.messages.count_documents({
                        "user": user["user_id"],
                        "datetime": {"$gte": datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)}
                    })
                }
                users.append(user_stats)
            return users
        except Exception as e:
            logger.error(f"Error getting user list: {e}")
            return []

    def broadcast_message(self, message: str) -> Dict[str, int]:
        try:
            users = self.db.users.find({})
            success_count = 0
            fail_count = 0
            
            for user in users:
                try:
                    # Note: actual message sending will be handled by the bot
                    success_count += 1
                except Exception:
                    fail_count += 1
                    
            return {
                "success": success_count,
                "failed": fail_count
            }
        except Exception as e:
            logger.error(f"Error broadcasting message: {e}")
            return {"success": 0, "failed": 0}
