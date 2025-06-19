import json
import os
import logging
from typing import List, Dict, Optional, Any
from datetime import datetime

# Configure logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

class UserManager:
    """Manages user access to the bot"""
    
    def __init__(self, filename: str = "approved_users.json"):
        """Initialize the user manager with a storage file"""
        self.filename = filename
        self.users = self._load_users()
    
    def _load_users(self) -> Dict[str, Dict[str, Any]]:
        """Load users from the storage file"""
        try:
            with open(self.filename, "r") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}
    
    def _save_users(self) -> None:
        """Save users to the storage file"""
        try:
            with open(self.filename, "w") as f:
                json.dump(self.users, f, indent=4, default=str)
            logger.info(f"Saved {len(self.users)} users to {self.filename}")
        except Exception as e:
            logger.error(f"Error saving users to {self.filename}: {e}")
    
    def add_user(self, user_id: int, username: Optional[str] = None, first_name: Optional[str] = None) -> None:
        """Add a new user to the approved users list"""
        self.users[str(user_id)] = {
            "username": username,
            "first_name": first_name,
            "added_at": datetime.now().isoformat(),
            "subscription_expiry": None
        }
        self._save_users()
    
    def remove_user(self, user_id: int) -> bool:
        """Remove a user from the approved users list"""
        if str(user_id) in self.users:
            del self.users[str(user_id)]
            self._save_users()
            logger.info(f"User {user_id} removed from approved users")
            return True
        logger.info(f"User {user_id} not found in approved users")
        return False
    
    def is_user_approved(self, user_id: int) -> bool:
        """Check if a user is approved and has valid subscription"""
        user = self.users.get(str(user_id))
        if not user:
            return False
            
        expiry = user.get("subscription_expiry")
        if expiry is None:
            return False
            
        if expiry == "lifetime":
            return True
            
        try:
            expiry_date = datetime.fromisoformat(expiry)
            return datetime.now() <= expiry_date
        except (ValueError, TypeError):
            return False
    
    def update_user_subscription(self, user_id: int, expiry: datetime) -> None:
        """Update user's subscription expiry date"""
        if str(user_id) not in self.users:
            raise ValueError("User not found")
            
        self.users[str(user_id)]["subscription_expiry"] = (
            "lifetime" if expiry == datetime.max else expiry.isoformat()
        )
        self._save_users()
    
    def get_user_subscription_status(self, user_id: int) -> Dict[str, Any]:
        """Get user's subscription status"""
        user = self.users.get(str(user_id))
        if not user:
            return {"active": False, "expiry": None}
            
        expiry = user.get("subscription_expiry")
        if expiry is None:
            return {"active": False, "expiry": None}
            
        if expiry == "lifetime":
            return {"active": True, "expiry": "lifetime"}
            
        try:
            expiry_date = datetime.fromisoformat(expiry)
            return {
                "active": datetime.now() <= expiry_date,
                "expiry": expiry_date.isoformat()
            }
        except (ValueError, TypeError):
            return {"active": False, "expiry": None}
    
    def get_all_users(self) -> Dict[str, Dict[str, Any]]:
        """Get all users and their data"""
        return self.users
    
    def update_user_info(self, user_id: int, username: Optional[str] = None, first_name: Optional[str] = None, last_name: Optional[str] = None) -> bool:
        """Update user information if the user exists"""
        if str(user_id) not in self.users:
            return False
        
        user_data = self.users[str(user_id)]
        
        if username is not None:
            user_data["username"] = username
        if first_name is not None:
            user_data["first_name"] = first_name
        if last_name is not None:
            user_data["last_name"] = last_name
        
        self._save_users()
        return True

# Create a global instance
user_manager = UserManager()