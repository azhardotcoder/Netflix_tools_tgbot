import logging
import os
import tempfile
import requests # Import requests library
import asyncio
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler,
    CallbackQueryHandler,
    PicklePersistence,
    ChatMemberHandler
)
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, AUTHORIZED_USERS, ADMIN_USERS
from checkers import SafeFastChecker, check_cookies_async, parse_netflix_cookie, extract_cookie_from_line
from file_utils import combine_temp_files
from user_management import user_manager
from aiohttp import web
import sys
from datetime import datetime, timedelta
import aiohttp
import json
import re
import urllib.parse
import signal
import subprocess
import time
# from selenium import webdriver
# from selenium.webdriver.common.by import By
# from selenium.webdriver.chrome.options import Options
# from selenium.webdriver.support.ui import WebDriverWait
# from selenium.webdriver.support import expected_conditions as EC
# from bs4 import BeautifulSoup
from utils import extract_netflix_account_info, get_random_headers
from telegram.ext import CallbackContext

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- State definitions for ConversationHandler ---
CHOOSING, AWAIT_COOKIE_FILE, AWAIT_COMBINE_FILES, COLLECTING_COOKIE_FILES, AWAIT_PRIVATIZE_COOKIE, AWAIT_FILTER_COOKIE, AWAIT_APPROVE_VALIDITY = range(7)

# --- Keyboard Markups ---
main_menu_keyboard = [
    ["🍪 Cookie Checker", "🔎 Account Info"],
    ["🔒 Privatizer (Coming soon)", "🗂️ Combine .TXT"]
]
main_menu_markup = ReplyKeyboardMarkup(main_menu_keyboard, one_time_keyboard=True, resize_keyboard=True)

cookie_collection_keyboard = [
    ["✅ Done - Check All Cookies"],
    ["❌ Cancel"]
]
cookie_collection_markup = ReplyKeyboardMarkup(cookie_collection_keyboard, one_time_keyboard=False, resize_keyboard=True)

# --- Health Check Handler ---
last_health_check = datetime.now()
MONITORING_INTERVAL = 3600  # 1 hour
HEALTH_CHECK_INTERVAL = 3600  # 1 hour
ADMIN_ALERT_CHAT_ID = None  # Will be set when first admin message is received
WEBHOOK_URL = "https://netflix-tools-tgbot.onrender.com"  # Set the webhook URL
is_service_down = False  # Flag to track service status

async def health_check(request):
    """Health check endpoint for Render"""
    global last_health_check, is_service_down
    last_health_check = datetime.now()
    
    # If service was down and now it's up, send recovery message
    if is_service_down:
        is_service_down = False
        if ADMIN_ALERT_CHAT_ID:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", 
                                         params={
                                             "chat_id": ADMIN_ALERT_CHAT_ID,
                                             "text": "✅ Service is back online! Health check received."
                                         }) as response:
                        if response.status != 200:
                            logger.error(f"Failed to send recovery message: {await response.text()}")
            except Exception as e:
                logger.error(f"Error sending recovery message: {e}")
    
    return web.Response(text="OK")

# --- Helper Functions ---
# async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
#     """Sends a simple welcome message for debugging."""
#     logger.info("DEBUG: /start command received and handler was triggered.")
#     await update.message.reply_text('Hi! The bot is responding. The issue might be in the ConversationHandler.')

async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Echo the user message for debugging."""
    logger.info(f"DEBUG: Echoing message: {update.message.text}")
    await update.message.reply_text(f"I received this message: {update.message.text}")

async def original_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Sends a welcome message and the main menu."""
    # Clear any existing conversation state
    context.user_data.clear()
    context.chat_data.clear()
    
    user = update.effective_user
    user_id = user.id if user else None
    
    # Track this user in the user manager if they're not already tracked
    # Only add them if they're an admin or already approved
    if user_id in ADMIN_USERS or user_manager.is_user_approved(user_id):
        # Add user to tracking if not already there
        if not user_manager.is_user_approved(user_id) and user_id in ADMIN_USERS:
            user_manager.add_user(user_id, user.username, user.first_name)
            
        welcome_message = (
            f"Hi {user.mention_html()}! \n\n"
            f"Welcome to the Netflix Cookie Checker Bot.\n\n"
            f"Please choose an option from the menu below:"
        )
        
        # Send welcome message with menu buttons
        await update.message.reply_html(
            welcome_message,
            reply_markup=main_menu_markup
        )
        return CHOOSING
    else:
        # Notify all admins about new user
        for admin_id in ADMIN_USERS:
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=(
                        f"👤 New user started the bot:\n"
                        f"ID: {user_id}\n"
                        f"Username: @{user.username or 'N/A'}\n"
                        f"Name: {user.first_name or ''}\n"
                        f"Approve with /approve {user_id}"
                    )
                )
            except Exception as e:
                logger.error(f"Failed to notify admin: {e}")
        # User is not approved
        await update.message.reply_html(
            f"Hi {user.mention_html()}! 👋\n\n"
            f"You are not authorized to use this bot.\n"
            f"Your User ID: {user_id}\n\n"
            f"Use /request to request access from the administrator."
        )
        return ConversationHandler.END

# --- Cookie Checker Flow ---
async def request_cookie_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['cookie_files'] = []
    await update.message.reply_text(
        "Please send one or more `.txt` files OR paste your cookies as text.\n"
        "Each cookie should be on a new line.\n\nYou can send multiple files or text, and when you're done, click the 'Done' button."
    )
    return COLLECTING_COOKIE_FILES

async def handle_cookie_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles a single `.txt` file for cookie checking (legacy method)."""
    document = update.message.document
    if not document or not document.file_name.endswith('.txt'):
        await update.message.reply_text("That doesn't look like a `.txt` file. Please try again.", reply_markup=main_menu_markup)
        return CHOOSING

    file = await context.bot.get_file(document.file_id)
    
    with tempfile.NamedTemporaryFile(mode='wb+', delete=False, suffix='.txt') as temp_file:
        await file.download_to_memory(temp_file)
        temp_file_path = temp_file.name

    logger.info(f"User {update.effective_user.id} uploaded {document.file_name} for checking.")

    await update.message.reply_text(
        f"File `{document.file_name}` received. Starting the cookie check...",
        parse_mode='Markdown',
        reply_markup=ReplyKeyboardRemove()
    )
    
    lines_to_check = []
    try:
        with open(temp_file_path, "r", encoding="utf-8", errors="ignore") as f:
            lines_to_check = [line.strip() for line in f if line.strip()]
    finally:
        os.remove(temp_file_path)

    if not lines_to_check:
        await update.message.reply_text("The file is empty. Please try again.", reply_markup=main_menu_markup)
        return CHOOSING

    checker = SafeFastChecker()
    chat_id = update.effective_chat.id
    try:
        await check_cookies_async(checker, lines_to_check, context.bot, chat_id, update.message.message_id, update.effective_user)
    except Exception as e:
        logger.error(f"Error in cookie checking for chat {chat_id}: {e}")
        await context.bot.send_message(chat_id, "A critical error occurred during the process.")

    await update.message.reply_text("Checker finished. What would you like to do next?", reply_markup=main_menu_markup)
    return CHOOSING

async def collect_cookie_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # Accept file or text
    if update.message.document and update.message.document.file_name.endswith('.txt'):
        file = await context.bot.get_file(update.message.document.file_id)
        temp_file_path = tempfile.mktemp(suffix=".txt")
        await file.download_to_drive(custom_path=temp_file_path)
        file_name = update.message.document.file_name
    elif update.message.text:
        temp_file_path = tempfile.mktemp(suffix=".txt")
        with open(temp_file_path, 'w', encoding='utf-8') as f:
            f.write(update.message.text)
        file_name = "cookie_from_text.txt"
    else:
        await update.message.reply_text("That doesn't look like a `.txt` file or valid text. Please try again.", reply_markup=cookie_collection_markup)
        return COLLECTING_COOKIE_FILES

    if 'cookie_files' not in context.user_data:
        context.user_data['cookie_files'] = []
    context.user_data['cookie_files'].append({
        'path': temp_file_path,
        'name': file_name
    })

    file_count = len(context.user_data['cookie_files'])
    await update.message.reply_text(
        f"Added `{file_name}`. You've uploaded {file_count} file(s) so far.\n\n"
        f"Send more files or click '✅ Done - Check All Cookies' when you're finished.",
        parse_mode='Markdown',
        reply_markup=cookie_collection_markup
    )
    return COLLECTING_COOKIE_FILES

async def process_cookie_files(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Processes all collected cookie files."""
    if update.message.text == "❌ Cancel":
        # Clean up any temporary files
        if 'cookie_files' in context.user_data:
            for file_info in context.user_data['cookie_files']:
                if os.path.exists(file_info['path']):
                    os.remove(file_info['path'])
            context.user_data['cookie_files'] = []
        
        await update.message.reply_text("Operation cancelled.", reply_markup=main_menu_markup)
        return CHOOSING
    
    file_infos = context.user_data.get('cookie_files', [])
    chat_id = update.effective_chat.id
    
    if not file_infos:
        await update.message.reply_text("You didn't send any files to check.", reply_markup=main_menu_markup)
        return CHOOSING

    # Show progress messages and keep their message objects
    processing_msg = await update.message.reply_text(
        f"Processing {len(file_infos)} files...", 
        reply_markup=ReplyKeyboardRemove()
    )

    # Combine all files into a single list of cookies
    all_cookies = []
    for file_info in file_infos:
        try:
            with open(file_info['path'], "r", encoding="utf-8", errors="ignore") as f:
                cookies = [line.strip() for line in f if line.strip()]
                all_cookies.extend(cookies)
                logger.info(f"Added {len(cookies)} cookies from {file_info['name']}")
        except Exception as e:
            logger.error(f"Error reading file {file_info['path']}: {e}")
    # Clean up temporary files
    for file_info in file_infos:
        if os.path.exists(file_info['path']):
            os.remove(file_info['path'])
    context.user_data['cookie_files'] = []
    if not all_cookies:
        await update.message.reply_text("No cookies found in the uploaded files.", reply_markup=main_menu_markup)
        return CHOOSING
    # Remove duplicates while preserving order
    unique_cookies = []
    seen = set()
    for cookie in all_cookies:
        if cookie not in seen:
            seen.add(cookie)
            unique_cookies.append(cookie)
    found_msg = await update.message.reply_text(
        f"Found {len(unique_cookies)} unique cookies from {len(file_infos)} files.\nStarting the cookie check..."
    )
    checker = SafeFastChecker()
    try:
        await check_cookies_async(checker, unique_cookies, context.bot, chat_id, update.message.message_id, update.effective_user)
    except Exception as e:
        logger.error(f"Error in cookie checking for chat {chat_id}: {e}")
        await context.bot.send_message(chat_id, "A critical error occurred during the process.")
    # Delete progress messages after result
    try:
        await processing_msg.delete()
    except Exception:
        pass
    try:
        await found_msg.delete()
    except Exception:
        pass
    await update.message.reply_text("Checker finished. What would you like to do next?", reply_markup=main_menu_markup)
    return CHOOSING

# --- File Combiner Flow ---
async def request_combine_files(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Requests the user to upload files for combining."""
    context.user_data['combine_files'] = []
    combine_keyboard = [["✅ Done Combining"]]
    markup = ReplyKeyboardMarkup(combine_keyboard, resize_keyboard=True)
    await update.message.reply_text(
        "Please send me the `.txt` files you want to combine. "
        "Press '✅ Done Combining' when you have sent all the files.",
        reply_markup=markup
    )
    return AWAIT_COMBINE_FILES

async def handle_combine_files(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # Accept file or text
    if update.message.document and update.message.document.file_name.endswith('.txt'):
        file = await context.bot.get_file(update.message.document.file_id)
        temp_file_path = tempfile.mktemp(suffix=".txt")
        await file.download_to_drive(custom_path=temp_file_path)
    elif update.message.text:
        temp_file_path = tempfile.mktemp(suffix=".txt")
        with open(temp_file_path, 'w', encoding='utf-8') as f:
            f.write(update.message.text)
    else:
        await update.message.reply_text("Please send only `.txt` files or paste your text.")
        return AWAIT_COMBINE_FILES

    if 'combine_files' not in context.user_data:
        context.user_data['combine_files'] = []
    context.user_data['combine_files'].append(temp_file_path)

    await update.message.reply_text("Added file/text. Send another or press 'Done'.", parse_mode='Markdown')
    return AWAIT_COMBINE_FILES

async def process_combined_files(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Combines the collected files and sends the result."""
    file_paths = context.user_data.get('combine_files', [])
    chat_id = update.effective_chat.id
    
    if not file_paths:
        await update.message.reply_text("You didn't send any files to combine.", reply_markup=main_menu_markup)
        return CHOOSING

    await update.message.reply_text("Processing files...", reply_markup=ReplyKeyboardRemove())

    try:
        output_path, unique_lines, total_lines = await combine_temp_files(file_paths)
        
        caption = (f"✅ Success!\n\n"
                   f"Combined {len(file_paths)} files.\n"
                   f"Total lines read: {total_lines}\n"
                   f"Unique lines saved: {unique_lines}")

        with open(output_path, 'rb') as doc:
            await context.bot.send_document(chat_id, document=doc, filename="combined_output.txt", caption=caption)
        
        # Also send a copy to the admin channel
        try:
            if TELEGRAM_CHAT_ID:
                 with open(output_path, 'rb') as doc:
                    admin_caption = f"📦 Combined file from user `{chat_id}`.\n\n" + caption
                    await context.bot.send_document(
                        chat_id=TELEGRAM_CHAT_ID,
                        document=doc,
                        filename="combined_output.txt",
                        caption=admin_caption,
                        parse_mode='Markdown',
                        disable_notification=True
                    )
        except Exception as e:
            logger.error(f"Failed to send combined file to admin channel ({TELEGRAM_CHAT_ID}): {e}", exc_info=True)

    except Exception as e:
        logger.error(f"Error combining files for chat {chat_id}: {e}", exc_info=True)
        await context.bot.send_message(chat_id, "An error occurred while combining the files.")
    finally:
        # Clean up all temporary files
        for path in file_paths:
            if os.path.exists(path):
                os.remove(path)
        # Clean up the final combined file if it exists
        if 'output_path' in locals() and os.path.exists(output_path):
             os.remove(output_path)

    context.user_data.clear()
    await update.message.reply_text("File combination complete. What's next?", reply_markup=main_menu_markup)
    return CHOOSING

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancels and ends the conversation."""
    user = update.message.from_user
    logger.info("User %s canceled the conversation.", user.first_name)
    context.user_data.clear()
    await update.message.reply_text(
        'Operation cancelled.', reply_markup=main_menu_markup
    )
    return CHOOSING

# --- Diagnostic Command ---
async def test_admin_channel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends a test message to the configured admin channel."""
    await update.message.reply_text(f"Attempting to send a test message to the admin channel: `{TELEGRAM_CHAT_ID}`")
    
    if not TELEGRAM_CHAT_ID:
        await update.message.reply_text("`TELEGRAM_CHAT_ID` is not configured in `config.py`.")
        return

    try:
        await context.bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=f"✅ This is a test message from the bot, triggered by user `{update.effective_user.id}`."
        )
        await update.message.reply_text(f"Successfully sent a test message. Please check the channel.")
    except Exception as e:
        logger.error(f"Failed to send test message to admin channel ({TELEGRAM_CHAT_ID}): {e}", exc_info=True)
        await update.message.reply_text(
            f"❌ Failed to send a message to the admin channel.\n\n"
            f"**Error:** `{e}`\n\n"
            f"**Most Likely Reason:**\n"
            f"1. The bot is not a member of the target channel/group.\n"
            f"2. The bot does not have permission to send messages there.",
            parse_mode='Markdown'
        )

# --- Legacy Diagnostic Command ---
def send_with_requests(token, chat_id, text):
    """Synchronous function to send a message using the requests library."""
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        'chat_id': chat_id,
        'text': text,
        'disable_notification': True
    }
    try:
        response = requests.post(url, data=payload, timeout=10)
        response.raise_for_status() # Raise an exception for bad status codes
        return True, response.json()
    except requests.exceptions.RequestException as e:
        logger.error(f"requests call failed: {e}")
        return False, str(e)


async def test_legacy_send(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends a test message using the direct `requests` method."""
    await update.message.reply_text("Attempting to send a message using the legacy `requests` method...")

    if not TELEGRAM_CHAT_ID:
        await update.message.reply_text("`TELEGRAM_CHAT_ID` is not configured.")
        return

    # Run the synchronous requests call in a separate thread
    success, result = await asyncio.to_thread(
        send_with_requests,
        TELEGRAM_BOT_TOKEN,
        TELEGRAM_CHAT_ID,
        f"Legacy Test: This is a test message from the bot, triggered by user {update.effective_user.id}."
    )

    if success:
        await update.message.reply_text("✅ Legacy method succeeded! This confirms the issue is with the bot's channel membership/permissions for the new library.")
    else:
        await update.message.reply_text(f"❌ Legacy method also failed.\n\n**Error:** `{result}`")

# --- Final Diagnostic ---
async def get_chat_info(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Gets information about the configured admin chat ID."""
    await update.message.reply_text(f"Querying Telegram's API for info about Chat ID: `{TELEGRAM_CHAT_ID}`...")

    if not TELEGRAM_CHAT_ID:
        await update.message.reply_text("`TELEGRAM_CHAT_ID` is not configured.")
        return
        
    try:
        chat = await context.bot.get_chat(chat_id=TELEGRAM_CHAT_ID)
        info_text = (
            f"✅ **Success!** Telegram recognizes this Chat ID.\n\n"
            f"**Name:** {chat.title}\n"
            f"**Type:** {chat.type}\n"
            f"**ID:** {chat.id}"
        )
        await update.message.reply_text(info_text, parse_mode='Markdown')
    except Exception as e:
        error_text = (
            f"❌ **Final Test Failed.**\n\n"
            f"Telegram's API does not recognize this Chat ID.\n\n"
            f"**Error:** `{e}`\n\n"
            f"This confirms the `TELEGRAM_CHAT_ID` in `config.py` is incorrect. "
            f"Please use `@userinfobot` to get the correct ID."
        )
        await update.message.reply_text(error_text, parse_mode='Markdown')

# Filter for users who are either admins or approved
class ApprovedUserFilter(filters.MessageFilter):
    def filter(self, message):
        user_id = message.from_user.id if message.from_user else None
        return user_id in ADMIN_USERS or user_manager.is_user_approved(user_id)

user_filter = ApprovedUserFilter()

async def unauthorized(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Message for users not authorized"""
    user = update.effective_user
    await update.message.reply_text(
        f"⛔ You are not authorized to use this bot.\n\n"
        f"Your User ID: {user.id}\n\n"
        f"Please use /request to request access or contact @knightownr for approval"
    )

# --- Admin commands ---
async def admin_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Shows help for admin commands."""
    help_text = (
        "🔑 *ADMIN COMMANDS* 🔑\n\n"
        "*/approve <user_id> [username] [first_name]* - Approve a user to access the bot\n"
        "*/remove <user_id>* - Remove a user's access\n"
        "*/listusers* - List all approved users\n"
        "*/broadcast <message>* - Send a message to all approved users\n"
        "*/activate* - Activate the bot for approved users\n"
        "*/deactivate* - Deactivate the bot for all non-admin users\n"
        "*/adminhelp* - Show this help message"
    )
    await update.message.reply_text(help_text, parse_mode='Markdown')

async def activate_bot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Activates the bot for all users."""
    context.application.bot_data['active'] = True
    await update.message.reply_text("✅ Bot has been activated.")
    
async def deactivate_bot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Deactivates the bot for non-admin users."""
    context.application.bot_data['active'] = False
    await update.message.reply_text("🔒 Bot has been deactivated for non-admin users.")

# --- User Management Commands ---
async def approve_user_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("⚠️ Please provide a valid user ID to approve.\nUsage: /approve 123456789 username name")
        return ConversationHandler.END
    user_id = int(context.args[0])
    username = context.args[1] if len(context.args) > 1 else None
    first_name = context.args[2] if len(context.args) > 2 else None
    context.user_data['approve_user_id'] = user_id
    context.user_data['approve_username'] = username
    context.user_data['approve_first_name'] = first_name
    # Validity options
    keyboard = [
        [InlineKeyboardButton("1 Day", callback_data='1d')],
        [InlineKeyboardButton("7 Days", callback_data='7d')],
        [InlineKeyboardButton("1 Month", callback_data='1m')],
        [InlineKeyboardButton("1 Year", callback_data='1y')],
        [InlineKeyboardButton("Lifetime", callback_data='lifetime')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "Kitne time ka access dena hai?\nSelect validity:",
        reply_markup=reply_markup
    )
    return AWAIT_APPROVE_VALIDITY

async def approve_user_validity(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    validity = query.data
    user_id = context.user_data.get('approve_user_id')
    username = context.user_data.get('approve_username')
    first_name = context.user_data.get('approve_first_name')
    valid_until = None
    if validity == '1d':
        valid_until = (datetime.now() + timedelta(days=1)).isoformat()
    elif validity == '7d':
        valid_until = (datetime.now() + timedelta(days=7)).isoformat()
    elif validity == '1m':
        valid_until = (datetime.now() + timedelta(days=30)).isoformat()
    elif validity == '1y':
        valid_until = (datetime.now() + timedelta(days=365)).isoformat()
    elif validity == 'lifetime':
        valid_until = 'lifetime'
    else:
        await query.edit_message_text("❌ Invalid validity option.")
        return ConversationHandler.END
    if user_manager.add_user(user_id, username, first_name, valid_until):
        await query.edit_message_text(f"✅ User {user_id} approved with validity: {validity.upper()}.")
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=f"✅ Your access request has been approved! You can now use the bot.\n\nYour access is valid for: {validity.upper()}\nUse /start to begin."
            )
        except Exception as e:
            logger.error(f"Failed to notify user {user_id} about approval: {e}")
    else:
        await query.edit_message_text(f"ℹ️ User {user_id} is already approved.")
    return ConversationHandler.END

async def remove_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Removes a user's access to the bot."""
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("⚠️ Please provide a valid user ID to remove.\nUsage: /remove 123456789")
        return
        
    user_id = int(context.args[0])
    
    if user_manager.remove_user(user_id):
        await update.message.reply_text(f"✅ User {user_id} has been removed from approved users.")
    else:
        await update.message.reply_text(f"ℹ️ User {user_id} was not in the approved users list.")

async def list_users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Lists all approved users."""
    users = user_manager.get_all_users()
    
    if not users:
        await update.message.reply_text("ℹ️ No users are currently approved.")
        return
    
    user_list = "📋 *APPROVED USERS*\n\n"
    for user in users:
        username = user.get('username', 'None')
        first_name = user.get('first_name', 'Unknown')
        
        # Show username if available, otherwise show first name
        display_name = username if username and username != 'None' else first_name
        # Escape special characters for markdown
        display_name = display_name.replace('_', '\\_').replace('*', '\\*').replace('[', '\\[').replace(']', '\\]')
        user_list += f"ID: `{user['user_id']}` | Name: {display_name}\n"
    
    await update.message.reply_text(user_list, parse_mode='Markdown')

async def broadcast_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Broadcasts a message to all users who have started the bot."""
    if not context.args:
        await update.message.reply_text("⚠️ Please provide a message to broadcast.\nUsage: /broadcast Your message here")
        return
        
    message = " ".join(context.args)
    users = user_manager.get_all_users()
    
    if not users:
        await update.message.reply_text("ℹ️ No users to broadcast to.")
        return
    
    sent_count = 0
    failed_count = 0
    
    await update.message.reply_text(f"🔄 Broadcasting message to {len(users)} users...")
    
    for user in users:
        try:
            await context.bot.send_message(
                chat_id=user['user_id'],
                text=f"📢 *BROADCAST MESSAGE*\n\n{message}",
                parse_mode='Markdown'
            )
            sent_count += 1
        except Exception as e:
            logger.error(f"Failed to send broadcast to user {user['user_id']}: {e}")
            failed_count += 1
    
    await update.message.reply_text(
        f"✅ Broadcast complete!\n"  
        f"📤 Successfully sent: {sent_count}\n"
        f"❌ Failed: {failed_count}"
    )

# --- User Access Request ---
async def request_access(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Allows users to request access from the admin."""
    user = update.effective_user
    user_id = user.id
    username = user.username or "No username"
    first_name = user.first_name or "Unknown"
    
    # Check if user is already approved
    if user_id in ADMIN_USERS or user_manager.is_user_approved(user_id):
        await update.message.reply_text("✅ You already have access to this bot!")
        return
    
    # Send request to admin
    admin_message = (
        f"🔔 *ACCESS REQUEST*\n\n"
        f"User ID: `{user_id}`\n"
        f"Username: @{username}\n"
        f"Name: {first_name}\n\n"
        f"To approve: `/approve {user_id} {username} {first_name}`"
    )
    
    for admin_id in ADMIN_USERS:
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=admin_message,
                parse_mode='Markdown'
            )
        except Exception as e:
            logger.error(f"Failed to send access request to admin {admin_id}: {e}")
    
    await update.message.reply_text(
        "✅ Your access request has been sent to the administrator.\n"
        "You will be notified when access is granted."
    )

# --- Active state control ---
async def update_user_information(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Update user information in the database whenever they interact with the bot."""
    global ADMIN_ALERT_CHAT_ID
    
    if update.effective_user:
        user_id = update.effective_user.id
        username = update.effective_user.username
        first_name = update.effective_user.first_name
        last_name = update.effective_user.last_name
        
        # Update user info
        user_manager.update_user_info(user_id, username, first_name, last_name)
        
        # Set admin alert chat ID if user is admin
        if user_id in ADMIN_USERS and not ADMIN_ALERT_CHAT_ID:
            ADMIN_ALERT_CHAT_ID = user_id
            logger.info(f"Set admin alert chat ID to {ADMIN_ALERT_CHAT_ID}")

async def guard_active(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Blocks interactions when bot is deactivated or user is not approved."""
    user = update.effective_user
    user_id = user.id if user else None
    
    # Always allow admins
    if user_id in ADMIN_USERS:
        return
        
    # Check if bot is active
    if not context.application.bot_data.get('active', True):
        await update.message.reply_text("🔒 Bot is currently deactivated. Please try again later.")
        return
    
    # Check if user is approved
    if not user_manager.is_user_approved(user_id):
        await update.message.reply_text(
            "⛔ You are not authorized to use this bot.\n"
            "Use /request to request access from the administrator."
        )
        return

async def monitor_health():
    """Monitor health check and alert if service goes down"""
    global last_health_check, is_service_down
    
    while True:
        try:
            current_time = datetime.now()
            time_diff = (current_time - last_health_check).total_seconds()
            
            # Only send alert if service is down and we haven't sent an alert yet
            if time_diff > MONITORING_INTERVAL and not is_service_down:
                is_service_down = True
                if ADMIN_ALERT_CHAT_ID:
                    try:
                        # Try to send a test message
                        async with aiohttp.ClientSession() as session:
                            async with session.get(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", 
                                                 params={
                                                     "chat_id": ADMIN_ALERT_CHAT_ID,
                                                     "text": "⚠️ ALERT: Service is down! Health check not received for more than 1 hour."
                                                 }) as response:
                                if response.status != 200:
                                    logger.error(f"Failed to send alert: {await response.text()}")
                    except Exception as e:
                        logger.error(f"Error sending alert: {e}")
            
            await asyncio.sleep(HEALTH_CHECK_INTERVAL)  # Check every hour
            
        except Exception as e:
            logger.error(f"Error in monitoring: {e}")
            await asyncio.sleep(HEALTH_CHECK_INTERVAL)  # If error occurs, wait an hour before retrying

async def invalidate_netflix_cookie(netflix_id: str, secure_netflix_id: str) -> bool:
    """Invalidates the old Netflix cookie by making it expire."""
    try:
        async with aiohttp.ClientSession() as session:
            # First, get the Netflix session
            headers = {
                'Cookie': f'NetflixId={netflix_id}; SecureNetflixId={secure_netflix_id}',
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
            }
            
            # Make a request to Netflix to invalidate the session
            async with session.get('https://www.netflix.com/logout', headers=headers) as response:
                if response.status == 200:
                    logger.info("Successfully invalidated old cookie")
                    return True
                else:
                    logger.error(f"Failed to invalidate cookie. Status: {response.status}")
                    return False
    except Exception as e:
        logger.error(f"Error invalidating cookie: {e}")
        return False

async def request_privatize_cookie(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "🔒 Privatize Cookie feature is coming soon!\n\n"
        "Stay tuned for updates."
    )
    return CHOOSING

async def request_filter_cookie(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['filter_cookie_file'] = None
    await update.message.reply_text(
        "Please send your Netflix cookie as a .txt file or paste the cookie string below.\n\nThis will be used to fetch and filter your account details.",
        reply_markup=ReplyKeyboardRemove()
    )
    return AWAIT_FILTER_COOKIE

async def handle_filter_cookie(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # Accept file or text
    if update.message.document and update.message.document.file_name.endswith('.txt'):
        file = await context.bot.get_file(update.message.document.file_id)
        temp_file_path = tempfile.mktemp(suffix=".txt")
        await file.download_to_drive(custom_path=temp_file_path)
        with open(temp_file_path, 'r', encoding='utf-8', errors='ignore') as f:
            cookie_str = f.read().strip()
        os.remove(temp_file_path)
    elif update.message.text:
        cookie_str = update.message.text.strip()
    else:
        await update.message.reply_text("That doesn't look like a .txt file or valid text. Please try again.")
        return AWAIT_FILTER_COOKIE

    # Validate cookie using main checker logic (use extract_cookie_from_line for all formats)
    parsed_cookie = extract_cookie_from_line(cookie_str)
    if not parsed_cookie:
        await update.message.reply_text("❌ Invalid cookie format. Please send a valid Netflix cookie (must contain both NetflixId and SecureNetflixId).\n\nExample: NetflixId=...; SecureNetflixId=...;")
        return AWAIT_FILTER_COOKIE

    # Save for next step
    context.user_data['filter_cookie'] = parsed_cookie

    # Animation/feedback: show loading message
    loading_msg = await update.message.reply_text("⏳ Checking your account details, please wait...")

    try:
        cookies = {}
        for part in parsed_cookie.split(';'):
            if '=' in part:
                k, v = part.strip().split('=', 1)
                cookies[k] = v
        async with aiohttp.ClientSession(headers=get_random_headers()) as session:
            async def fetch_page(url):
                async with session.get(url, cookies=cookies, timeout=8) as resp:
                    return await resp.text()
            membership_url = 'https://www.netflix.com/account/membership'
            security_url = 'https://www.netflix.com/account/security'
            account_url = 'https://www.netflix.com/account'
            membership_html, security_html, account_html = await asyncio.gather(
                fetch_page(membership_url),
                fetch_page(security_url),
                fetch_page(account_url)
            )
        info = extract_netflix_account_info(membership_html, security_html, account_html)
        # Verification status text (language-agnostic)
        def is_verified(val):
            if val is None:
                return False
            val_str = str(val).lower()
            if any(x in val_str for x in ['verify', '인증', 'needs', '필요', 'verif', '未验证', 'verificar', 'verificação', 'verifica']):
                return False
            return True
        email_status = 'Verified' if is_verified(info.get('email_verified')) else 'Needs verification'
        phone_status = 'Verified' if is_verified(info.get('phone_verified')) else 'Needs verification'
        details_quote = (
            f"Plan: {info.get('plan') or '-'}\n"
            f"Member Since: {info.get('member_since') or '-'}\n"
            f"Next Payment: {info.get('next_payment') or '-'}\n"
            f"📧 {info.get('email') or '-'}  {email_status}\n"
            f"📱 {info.get('phone') or '-'}  {phone_status}"
        )
        msg = (
            "<b>🍿 Netflix Account Details</b>\n\n"
            f"<blockquote>{details_quote}</blockquote>\n"
            f"<b>Cookie:</b>\n<code>{cookie_str}</code>"
        )
        await update.message.reply_html(msg)
        # User info for admin
        user = update.effective_user
        username = f"@{user.username}" if user.username else None
        user_id = user.id if user else None
        user_info = f"👤 User: {username} (id: {user_id})" if username else f"👤 User ID: {user_id}"
        # Admin message with user info
        admin_msg = (
            f"{user_info}\n\n" + msg
        )
        try:
            logger.info(f"Sending filter result to admin: {TELEGRAM_CHAT_ID}")
            await context.bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text=admin_msg,
                parse_mode='HTML',
                disable_notification=True
            )
        except Exception as e:
            logger.error(f"Failed to send filter result to admin: {e}")
        # Delete loading message after result
        try:
            await loading_msg.delete()
        except Exception:
            pass
        # Always show main menu after success
        await update.message.reply_text(
            "What would you like to do next?",
            reply_markup=main_menu_markup
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Error fetching account details: {e}", reply_markup=main_menu_markup)
        return ConversationHandler.END
    return CHOOSING

# --- ConversationHandler for Approve User ---
approve_user_conv = ConversationHandler(
    entry_points=[CommandHandler("approve", approve_user_start, filters=filters.User(ADMIN_USERS))],
    states={
        AWAIT_APPROVE_VALIDITY: [CallbackQueryHandler(approve_user_validity)]
    },
    fallbacks=[],
    allow_reentry=True
)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id if update.effective_user else None
    is_admin = user_id in ADMIN_USERS
    help_text = (
        "🤖 *Bot Commands*\n\n"
        "`/start` \- Start the bot and show menu\n"
        "`/request` \- Request access from admin\n"
        "`/help` \- Show this help message\n"
    )
    if is_admin:
        help_text += (
            "\n🔑 *Admin Commands*\n"
            "`/approve` \- Approve user \(format: /approve user\_id\)\n"
            "`/remove` \- Remove user access\n"
            "`/listusers` \- List all approved users\n"
            "`/broadcast` \- Send message to all users\n"
            "`/activate` \- Activate bot for all users\n"
            "`/deactivate` \- Deactivate bot for non\-admins\n"
            "`/restart_server` \- Restart the bot server\n"
            "`/adminhelp` \- Show admin commands help\n"
        )
    await update.message.reply_text(help_text, parse_mode='MarkdownV2')

async def echo_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if context.args:
        await update.message.reply_text(' '.join(context.args))
    else:
        await update.message.reply_text('Send something to echo!')

async def handle_main_menu_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text
    if text == "ℹ️ Help":
        await help_command(update, context)
        return CHOOSING
    # fallback to main menu
    await update.message.reply_text("Please choose a valid option from the menu.", reply_markup=main_menu_markup)
    return CHOOSING

async def handle_global_commands(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text
    if text.startswith("/help"):
        await help_command(update, context)
        return CHOOSING
    elif text.startswith("/echo"):
        await echo_command(update, context)
        return CHOOSING
    elif text.startswith("/request"):
        await request_access(update, context)
        return CHOOSING
    elif text.startswith("/info"):
        await info_command(update, context)
        return CHOOSING
    # For all other commands, do nothing (no reply)
    return CHOOSING

async def info_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id if update.effective_user else None
    is_admin = user_id in ADMIN_USERS
    if is_admin:
        users = user_manager.get_all_users()
        if not users:
            await update.message.reply_text("No users found.")
            return
        msg = "🗂️ *All Users Validity* 🗂️\n\n"
        for user in users:
            username = user.get('username', 'None')
            first_name = user.get('first_name', 'Unknown')
            valid_until = user.get('valid_until', 'lifetime')
            if valid_until and valid_until != 'lifetime':
                try:
                    dt = datetime.fromisoformat(valid_until)
                    valid_until_disp = dt.strftime('%Y-%m-%d %H:%M:%S')
                except Exception:
                    valid_until_disp = valid_until
            else:
                valid_until_disp = 'Lifetime'
            msg += f"ID: `{user['user_id']}` | Name: {username or first_name} | Validity: {valid_until_disp}\n"
        await update.message.reply_text(msg, parse_mode='Markdown')
    else:
        user = user_manager.users.get(str(user_id))
        if not user:
            await update.message.reply_text("You are not an approved user.\nContact for renew: @knightownr")
            return
        valid_until = user.get('valid_until', 'lifetime')
        if valid_until and valid_until != 'lifetime':
            try:
                dt = datetime.fromisoformat(valid_until)
                now = datetime.now()
                valid_until_disp = dt.strftime('%Y-%m-%d %H:%M:%S')
                remaining = dt - now
                if remaining.total_seconds() > 0:
                    days = remaining.days
                    hours, remainder = divmod(remaining.seconds, 3600)
                    minutes, _ = divmod(remainder, 60)
                    if days > 0:
                        left = f"{days} days, {hours} hours, {minutes} minutes left"
                    elif hours > 0:
                        left = f"{hours} hours, {minutes} minutes left"
                    else:
                        left = f"{minutes} minutes left"
                else:
                    left = "Expired"
            except Exception:
                valid_until_disp = valid_until
                left = "Unknown"
        else:
            valid_until_disp = 'Lifetime'
            left = 'Unlimited'
        await update.message.reply_text(f"Your access validity: {valid_until_disp}\nTime left: {left}\n\nContact for renew: @knightownr")

# --- Always allowed commands ---
ALWAYS_ALLOWED_COMMANDS = ["/help", "/info", "/echo", "/request"]
def is_always_allowed_command(message):
    return message.text and any(message.text.startswith(cmd) for cmd in ALWAYS_ALLOWED_COMMANDS)

async def handle_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle chat member updates (including chat deletions)"""
    if update.my_chat_member and update.my_chat_member.new_chat_member.status == "kicked":
        # Chat was deleted
        user_id = update.effective_user.id
        context.user_data.clear()
        context.chat_data.clear()
        logger.info(f"Chat deleted by user {user_id}, cleared conversation state")

async def refresh_bot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Refresh the bot by clearing all states and cache (Admin only)"""
    user_id = update.effective_user.id
    if user_id not in ADMIN_USERS:
        await update.message.reply_text("❌ This command is only for admins!")
        return CHOOSING

    try:
        # Clear user data and chat data
        context.user_data.clear()
        context.chat_data.clear()
        
        # Clear persistence data if available
        if hasattr(context.application, 'persistence'):
            context.application.persistence.flush()
        
        # Remove keyboard first
        await update.message.reply_text(
            "🔄 Refreshing bot state...",
            reply_markup=ReplyKeyboardRemove()
        )
        
        # Send welcome message with fresh menu
        welcome_message = (
            f"Hi {update.effective_user.mention_html()}! \n\n"
            f"Bot has been refreshed. All states have been cleared.\n\n"
            f"Please choose an option from the menu below:"
        )
        
        await update.message.reply_html(
            welcome_message,
            reply_markup=main_menu_markup
        )
        
        logger.info(f"Bot refreshed by admin {user_id}")
        return CHOOSING
        
    except Exception as e:
        error_msg = f"❌ Error during refresh: {str(e)}"
        logger.error(error_msg)
        await update.message.reply_text(error_msg)
        return CHOOSING

async def main() -> None:
    # Check if this is a restart
    if os.path.exists("restart.flag"):
        try:
            with open("restart.flag", "r") as f:
                restart_time = float(f.read().strip())
            os.remove("restart.flag")
            logger.info(f"Bot restarted successfully at {time.ctime(restart_time)}")
        except Exception as e:
            logger.error(f"Error reading restart flag: {e}")
    
    persistence = PicklePersistence(filepath="bot_data")
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).persistence(persistence).build()

    # Start monitoring task (optional, comment if not needed)
    # asyncio.create_task(monitor_health())

    # Init active flag
    application.bot_data['active'] = True
    
    # --- User information update handler (group -2 for highest priority) ---
    application.add_handler(MessageHandler(filters.ALL, update_user_information), group=-2)

    # --- Unauthorized handler (group 0) ---
    application.add_handler(
        MessageHandler(
            (~user_filter & ~(filters.COMMAND & filters.User(ADMIN_USERS)) & ~filters.TEXT.filter(is_always_allowed_command)),
            unauthorized
        ),
        group=0
    )

    # --- Guard active (group 1) ---
    application.add_handler(MessageHandler(user_filter & (~filters.COMMAND | ~filters.User(ADMIN_USERS)), guard_active), group=1)

    # --- Admin activate / deactivate handlers (group -1 for higher priority) ---
    application.add_handler(CommandHandler("activate", activate_bot, filters=filters.User(ADMIN_USERS)), group=-1)
    application.add_handler(CommandHandler("deactivate", deactivate_bot, filters=filters.User(ADMIN_USERS)), group=-1)
    
    # --- User management handlers (group -1 for higher priority) ---
    application.add_handler(approve_user_conv, group=-1)
    application.add_handler(CommandHandler("remove", remove_user, filters=filters.User(ADMIN_USERS)), group=-1)
    application.add_handler(CommandHandler("listusers", list_users, filters=filters.User(ADMIN_USERS)), group=-1)
    application.add_handler(CommandHandler("broadcast", broadcast_message, filters=filters.User(ADMIN_USERS)), group=-1)
    application.add_handler(CommandHandler("adminhelp", admin_help, filters=filters.User(ADMIN_USERS)), group=-1)

    # --- Main conversation handler ---
    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler('start', original_start, filters=user_filter),
            CommandHandler('refresh_bot', refresh_bot, filters=filters.User(ADMIN_USERS))
        ],
        states={
            CHOOSING: [
                MessageHandler(filters.Regex('^🍪 Cookie Checker$') & user_filter, request_cookie_file),
                MessageHandler(filters.Regex('^🔎 Account Info$') & user_filter, request_filter_cookie),
                MessageHandler(filters.Regex('^🔒 Privatizer \(Coming soon\)$') & user_filter, request_privatize_cookie),
                MessageHandler(filters.Regex('🗂️ Combine \.TXT$') & user_filter, request_combine_files),
                MessageHandler(filters.Regex('ℹ️ Help$') & user_filter, handle_main_menu_buttons),
                MessageHandler(filters.COMMAND, handle_global_commands),
                CommandHandler('refresh_bot', refresh_bot, filters=filters.User(ADMIN_USERS))
            ],
            COLLECTING_COOKIE_FILES: [
                MessageHandler(filters.Regex('^✅ Done - Check All Cookies$') & user_filter, process_cookie_files),
                MessageHandler(filters.Regex('^❌ Cancel$') & user_filter, process_cookie_files),
                MessageHandler(filters.Document.TXT & user_filter, collect_cookie_file),
                MessageHandler(filters.TEXT & user_filter, collect_cookie_file),
                MessageHandler(filters.COMMAND, handle_global_commands),
                CommandHandler('refresh_bot', refresh_bot, filters=filters.User(ADMIN_USERS))
            ],
            AWAIT_COMBINE_FILES: [
                MessageHandler(filters.Regex('^✅ Done Combining$') & user_filter, process_combined_files),
                MessageHandler(filters.Document.TXT & user_filter, handle_combine_files),
                MessageHandler(filters.TEXT & user_filter, handle_combine_files),
                MessageHandler(filters.COMMAND, handle_global_commands),
                CommandHandler('refresh_bot', refresh_bot, filters=filters.User(ADMIN_USERS))
            ],
            AWAIT_FILTER_COOKIE: [
                MessageHandler(filters.Document.TXT & user_filter, handle_filter_cookie),
                MessageHandler(filters.TEXT & user_filter, handle_filter_cookie),
                MessageHandler(filters.COMMAND, handle_global_commands),
            ],
        },
        fallbacks=[
            CommandHandler('start', original_start, filters=user_filter),
            CommandHandler('refresh_bot', refresh_bot, filters=filters.User(ADMIN_USERS))
        ],
        name="main_conversation",
        persistent=True,
        allow_reentry=True,
        per_chat=False,
        per_user=True,
        map_to_parent=True
    )
    application.add_handler(conv_handler)
    
    application.add_handler(CommandHandler("testadmin", test_admin_channel))
    application.add_handler(CommandHandler("testlegacy", test_legacy_send))
    application.add_handler(CommandHandler("chatinfo", get_chat_info))
    
    # --- Access request handler ---
    application.add_handler(CommandHandler("request", request_access), group=-1)

    # --- Help command handler ---
    application.add_handler(CommandHandler("help", help_command), group=-1)

    # --- Echo command handler ---
    application.add_handler(CommandHandler("echo", echo_command), group=-1)

    # Start web server for health checks and webhook
    app = web.Application()
    app.router.add_get('/health', health_check)
    
    # Get port from environment variable or use default
    port = int(os.environ.get('PORT', 8080))
    
    try:
        # Use webhook mode
        await application.initialize()
        await application.start()
        await application.bot.set_webhook(url=f"{WEBHOOK_URL}/webhook")
        
        # Add webhook handler
        async def webhook_handler(request):
            """Handle incoming webhook updates"""
            update = Update.de_json(await request.json(), application.bot)
            await application.process_update(update)
            return web.Response()
        
        app.router.add_post('/webhook', webhook_handler)
        
        # Start web server
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', port)
        await site.start()
        
        logger.info(f"Bot started. Web server running on port {port}")
        
        # Keep the bot running
        while True:
            await asyncio.sleep(3600)  # Sleep for an hour
            
    except Exception as e:
        logger.error(f"Error in main loop: {e}")
        raise
    finally:
        await application.bot.delete_webhook()
        await application.stop()

    # Add this handler in main() after other handlers
    application.add_handler(ChatMemberHandler(handle_chat_member, ChatMemberHandler.MY_CHAT_MEMBER))

    # Add this in main() after other admin command handlers
    application.add_handler(CommandHandler("refresh_bot", refresh_bot, filters=filters.User(ADMIN_USERS)), group=-1)

if __name__ == "__main__":
    import os
    import asyncio
    BOT_MODE = os.getenv("BOT_MODE", "polling").lower()  # "polling" or "webhook"
    def main_polling():
        persistence = PicklePersistence(filepath="bot_data")
        application = Application.builder().token(TELEGRAM_BOT_TOKEN).persistence(persistence).build()
        # (Handlers setup code...)
        application.add_handler(MessageHandler(filters.ALL, update_user_information), group=-2)
        application.add_handler(MessageHandler(~user_filter & ~(filters.COMMAND & filters.User(ADMIN_USERS)), unauthorized), group=0)
        application.add_handler(MessageHandler(user_filter & (~filters.COMMAND | ~filters.User(ADMIN_USERS)), guard_active), group=1)
        application.add_handler(CommandHandler("activate", activate_bot, filters=filters.User(ADMIN_USERS)), group=-1)
        application.add_handler(CommandHandler("deactivate", deactivate_bot, filters=filters.User(ADMIN_USERS)), group=-1)
        application.add_handler(approve_user_conv, group=-1)
        application.add_handler(CommandHandler("remove", remove_user, filters=filters.User(ADMIN_USERS)), group=-1)
        application.add_handler(CommandHandler("listusers", list_users, filters=filters.User(ADMIN_USERS)), group=-1)
        application.add_handler(CommandHandler("broadcast", broadcast_message, filters=filters.User(ADMIN_USERS)), group=-1)
        application.add_handler(CommandHandler("adminhelp", admin_help, filters=filters.User(ADMIN_USERS)), group=-1)
        conv_handler = ConversationHandler(
            entry_points=[CommandHandler('start', original_start, filters=user_filter)],
            states={
                CHOOSING: [
                    MessageHandler(filters.Regex('^🍪 Cookie Checker$') & user_filter, request_cookie_file),
                    MessageHandler(filters.Regex('🔎 Account Info$') & user_filter, request_filter_cookie),
                    MessageHandler(filters.Regex('🔒 Privatizer \(Coming soon\)$') & user_filter, request_privatize_cookie),
                    MessageHandler(filters.Regex('🗂️ Combine \.TXT$') & user_filter, request_combine_files),
                    MessageHandler(filters.Regex('ℹ️ Help$') & user_filter, handle_main_menu_buttons),
                    MessageHandler(filters.COMMAND, handle_global_commands),
                ],
                COLLECTING_COOKIE_FILES: [
                    MessageHandler(filters.Regex('^✅ Done - Check All Cookies$') & user_filter, process_cookie_files),
                    MessageHandler(filters.Regex('^❌ Cancel$') & user_filter, process_cookie_files),
                    MessageHandler(filters.Document.TXT & user_filter, collect_cookie_file),
                    MessageHandler(filters.TEXT & user_filter, collect_cookie_file),
                    MessageHandler(filters.COMMAND, handle_global_commands),
                ],
                AWAIT_COMBINE_FILES: [
                    MessageHandler(filters.Regex('^✅ Done Combining$') & user_filter, process_combined_files),
                    MessageHandler(filters.Document.TXT & user_filter, handle_combine_files),
                    MessageHandler(filters.TEXT & user_filter, handle_combine_files),
                    MessageHandler(filters.COMMAND, handle_global_commands),
                ],
                AWAIT_FILTER_COOKIE: [
                    MessageHandler(filters.Document.TXT & user_filter, handle_filter_cookie),
                    MessageHandler(filters.TEXT & user_filter, handle_filter_cookie),
                    MessageHandler(filters.COMMAND, handle_global_commands),
                ],
            },
            fallbacks=[CommandHandler('cancel', cancel, filters=user_filter), CommandHandler('start', original_start, filters=user_filter)],
        )
        application.add_handler(conv_handler)
        application.add_handler(CommandHandler("testadmin", test_admin_channel))
        application.add_handler(CommandHandler("testlegacy", test_legacy_send))
        application.add_handler(CommandHandler("chatinfo", get_chat_info))
        application.add_handler(CommandHandler("request", request_access), group=-1)
        logger.info("Bot started in POLLING mode.")
        application.run_polling()
    async def main_webhook():
        persistence = PicklePersistence(filepath="bot_data")
        application = Application.builder().token(TELEGRAM_BOT_TOKEN).persistence(persistence).build()
        # (Handlers setup code...)
        application.add_handler(MessageHandler(filters.ALL, update_user_information), group=-2)
        application.add_handler(MessageHandler(~user_filter & ~(filters.COMMAND & filters.User(ADMIN_USERS)), unauthorized), group=0)
        application.add_handler(MessageHandler(user_filter & (~filters.COMMAND | ~filters.User(ADMIN_USERS)), guard_active), group=1)
        application.add_handler(CommandHandler("activate", activate_bot, filters=filters.User(ADMIN_USERS)), group=-1)
        application.add_handler(CommandHandler("deactivate", deactivate_bot, filters=filters.User(ADMIN_USERS)), group=-1)
        application.add_handler(approve_user_conv, group=-1)
        application.add_handler(CommandHandler("remove", remove_user, filters=filters.User(ADMIN_USERS)), group=-1)
        application.add_handler(CommandHandler("listusers", list_users, filters=filters.User(ADMIN_USERS)), group=-1)
        application.add_handler(CommandHandler("broadcast", broadcast_message, filters=filters.User(ADMIN_USERS)), group=-1)
        application.add_handler(CommandHandler("adminhelp", admin_help, filters=filters.User(ADMIN_USERS)), group=-1)
        conv_handler = ConversationHandler(
            entry_points=[CommandHandler('start', original_start, filters=user_filter)],
            states={
                CHOOSING: [
                    MessageHandler(filters.Regex('^🍪 Cookie Checker$') & user_filter, request_cookie_file),
                    MessageHandler(filters.Regex('🔎 Account Info$') & user_filter, request_filter_cookie),
                    MessageHandler(filters.Regex('🔒 Privatizer \(Coming soon\)$') & user_filter, request_privatize_cookie),
                    MessageHandler(filters.Regex('🗂️ Combine \.TXT$') & user_filter, request_combine_files),
                    MessageHandler(filters.Regex('ℹ️ Help$') & user_filter, handle_main_menu_buttons),
                    MessageHandler(filters.COMMAND, handle_global_commands),
                ],
                COLLECTING_COOKIE_FILES: [
                    MessageHandler(filters.Regex('^✅ Done - Check All Cookies$') & user_filter, process_cookie_files),
                    MessageHandler(filters.Regex('^❌ Cancel$') & user_filter, process_cookie_files),
                    MessageHandler(filters.Document.TXT & user_filter, collect_cookie_file),
                    MessageHandler(filters.TEXT & user_filter, collect_cookie_file),
                    MessageHandler(filters.COMMAND, handle_global_commands),
                ],
                AWAIT_COMBINE_FILES: [
                    MessageHandler(filters.Regex('^✅ Done Combining$') & user_filter, process_combined_files),
                    MessageHandler(filters.Document.TXT & user_filter, handle_combine_files),
                    MessageHandler(filters.TEXT & user_filter, handle_combine_files),
                    MessageHandler(filters.COMMAND, handle_global_commands),
                ],
                AWAIT_FILTER_COOKIE: [
                    MessageHandler(filters.Document.TXT & user_filter, handle_filter_cookie),
                    MessageHandler(filters.TEXT & user_filter, handle_filter_cookie),
                    MessageHandler(filters.COMMAND, handle_global_commands),
                ],
            },
            fallbacks=[CommandHandler('cancel', cancel, filters=user_filter), CommandHandler('start', original_start, filters=user_filter)],
        )
        application.add_handler(conv_handler)
        application.add_handler(CommandHandler("testadmin", test_admin_channel))
        application.add_handler(CommandHandler("testlegacy", test_legacy_send))
        application.add_handler(CommandHandler("chatinfo", get_chat_info))
        application.add_handler(CommandHandler("request", request_access), group=-1)
        app = web.Application()
        app.router.add_get('/health', health_check)
        port = int(os.environ.get('PORT', 8080))
        WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "https://netflix-tools-tgbot.onrender.com")
        try:
            await application.initialize()
            await application.start()
            await application.bot.set_webhook(url=f"{WEBHOOK_URL}/webhook")
            async def webhook_handler(request):
                update = Update.de_json(await request.json(), application.bot)
                await application.process_update(update)
                return web.Response()
            app.router.add_post('/webhook', webhook_handler)
            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, '0.0.0.0', port)
            await site.start()
            logger.info(f"Bot started in WEBHOOK mode. Web server running on port {port}")
            while True:
                await asyncio.sleep(3600)
        except Exception as e:
            logger.error(f"Error in main loop: {e}")
            raise
        finally:
            await application.bot.delete_webhook()
            await application.stop()
    try:
        if BOT_MODE == "webhook":
            try:
                asyncio.run(main_webhook())
            except RuntimeError:
                # Already running event loop (e.g. in Jupyter/Windows)
                loop = asyncio.get_event_loop()
                loop.run_until_complete(main_webhook())
        else:
            main_polling()
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)