import json
import os
import logging
from typing import List, Dict, Optional
from datetime import datetime

# Configure logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

class UserManager:
    """Manages user access to the bot"""
    
    def __init__(self, storage_file: str = "approved_users.json"):
        """Initialize the user manager with a storage file"""
        self.storage_file = storage_file
        self.users: Dict[str, Dict] = {}
        self.load_users()
    
    def load_users(self) -> None:
        """Load users from the storage file"""
        if os.path.exists(self.storage_file):
            try:
                with open(self.storage_file, 'r') as f:
                    self.users = json.load(f)
                logger.info(f"Loaded {len(self.users)} users from {self.storage_file}")
            except Exception as e:
                logger.error(f"Error loading users from {self.storage_file}: {e}")
                self.users = {}
        else:
            logger.info(f"User storage file {self.storage_file} not found, starting with empty user list")
            self.users = {}
    
    def save_users(self) -> None:
        """Save users to the storage file"""
        try:
            with open(self.storage_file, 'w') as f:
                json.dump(self.users, f, indent=2)
            logger.info(f"Saved {len(self.users)} users to {self.storage_file}")
        except Exception as e:
            logger.error(f"Error saving users to {self.storage_file}: {e}")
    
    def add_user(self, user_id: int, username: Optional[str] = None, first_name: Optional[str] = None) -> bool:
        """Add a user to the approved list"""
        user_id_str = str(user_id)  # Convert to string for JSON compatibility
        
        if user_id_str in self.users:
            logger.info(f"User {user_id} already approved")
            return False
        
        self.users[user_id_str] = {
            "username": username,
            "first_name": first_name,
            "approved_at": str(datetime.now())
        }
        self.save_users()
        logger.info(f"User {user_id} ({username or first_name or 'Unknown'}) approved")
        return True
    
    def remove_user(self, user_id: int) -> bool:
        """Remove a user from the approved list"""
        user_id_str = str(user_id)  # Convert to string for JSON compatibility
        
        if user_id_str not in self.users:
            logger.info(f"User {user_id} not found in approved users")
            return False
        
        del self.users[user_id_str]
        self.save_users()
        logger.info(f"User {user_id} removed from approved users")
        return True
    
    def is_user_approved(self, user_id: int) -> bool:
        """Check if a user is approved"""
        return str(user_id) in self.users
    
    def get_all_users(self) -> List[Dict]:
        """Get all approved users with their details"""
        return [{
            "user_id": int(user_id),
            **user_data
        } for user_id, user_data in self.users.items()]
    
    def update_user_info(self, user_id: int, username: Optional[str] = None, first_name: Optional[str] = None) -> bool:
        """Update user information if the user exists"""
        user_id_str = str(user_id)  # Convert to string for JSON compatibility
        
        if user_id_str not in self.users:
            # User not found, nothing to update
            return False
        
        # Update user information if provided
        if username is not None:
            self.users[user_id_str]["username"] = username
        
        if first_name is not None:
            self.users[user_id_str]["first_name"] = first_name
        
        self.save_users()
        logger.info(f"Updated information for user {user_id} (username: {username}, first_name: {first_name})")
        return True

# Create a singleton instance
user_manager = UserManager()