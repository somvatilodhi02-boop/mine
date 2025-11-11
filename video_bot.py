# advanced_video_bot.py

import os
import requests
import logging
import time
import subprocess
from telegram import Update, Bot
from telegram.ext import (
    Updater,
    CommandHandler,
    MessageHandler,
    Filters,
    ConversationHandler,
    CallbackContext,
)

# --- CONFIGURATION for Self-Hosted Server ---
BOT_TOKEN = '7638957230:AAGrvjL9yebJnUaF2Gp5VgGWxxxggfQlQiw'
LOCAL_API_URL = "http://127.0.0.1:8081"
DOWNLOAD_PATH = './temp_large_downloads/'
FILE_TIMEOUT = 3600  # 1 hour to be safe

# --- BOT SETUP ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logger = logging.getLogger(__name__)

# Define states for our conversation
RECEIVE_LINK, PROCESSING = range(2)

# --- HELPER CLASSES AND FUNCTIONS ---

class UploadProgressWrapper:
    """A wrapper for a file object that provides an upload progress callback."""
    def __init__(self, fileobj, callback):
        self.fileobj = fileobj
        self.callback = callback
        self.bytes_read = 0
        self.total_size = os.fstat(fileobj.fileno()).st_size

    def read(self, size=-1):
        data = self.fileobj.read(size)
        self.bytes_read += len(data)
        self.callback(self.bytes_read, self.total_size)
        return data

    def __len__(self):
        return self.total_size

def human_readable_size(size, decimal_places=2):
    """Converts bytes to a human-readable format."""
    if size is None or size == 0: return "0 B"
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size < 1024.0: break
        size /= 1024.0
    return f"{size:.{decimal_places}f} {unit}"

def generate_thumbnail(video_path):
    """Generates a thumbnail from the middle of the video."""
    thumbnail_path = f"{video_path}.jpg"
    try:
        result = subprocess.run(
            ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', video_path],
            capture_output=True, text=True, check=True
        )
        duration = float(result.stdout.strip())
        midpoint = duration / 2
        subprocess.run(
            ['ffmpeg', '-ss', str(midpoint), '-i', video_path, '-vframes', '1', '-q:v', '2', thumbnail_path, '-y'],
            check=True, capture_output=True
        )
        logger.info(f"Thumbnail successfully generated at: {thumbnail_path}")
        return thumbnail_path
    except Exception as e:
        logger.error(f"FFmpeg error: {e}")
        return None

# --- BOT COMMANDS AND HANDLERS ---

def start_command(update: Update, context: CallbackContext) -> int:
    """Starts the conversation and asks for a video link."""
    welcome_message = (
        "👋 **Welcome to the Advanced Video Bot!**\n\n"
        "Send me a direct link to a video file (.mp4, etc.). I will download it, create a "
        "thumbnail, and upload it for you with progress updates.\n\n"
        "To stop any operation, you can use the /cancel command."
    )
    update.message.reply_text(welcome_message, parse_mode='Markdown')
    return RECEIVE_LINK

def help_command(update: Update, context: CallbackContext):
    """Displays a help message."""
    help_message = (
        "**How to use this bot:**\n\n"
        "1. Send a direct URL to a video file.\n"
        "2. The bot will show download progress.\n"
        "3. It will generate a thumbnail and then show upload progress.\n"
        "4. The video will appear in the chat.\n\n"
        "Use /cancel at any time to abort the current task."
    )
    update.message.reply_text(help_message, parse_mode='Markdown')

def process_link(update: Update, context: CallbackContext) -> int:
    """The main logic for downloading, processing, and uploading the video."""
    url = update.message.text
    chat_id = update.message.chat_id

    if not (url.startswith('http://') or url.startswith('https://')):
        update.message.reply_text("That doesn't look like a valid URL. Please send a direct link to a video file.")
        return RECEIVE_LINK

    status_message = update.message.reply_text("✅ Link received. Preparing...")
    context.user_data['status_message'] = status_message
    context.user_data['last_update_time'] = 0

    local_filepath = None
    thumbnail_filepath = None

    try:
        # --- 1. DOWNLOAD ---
        file_name = os.path.basename(url.split('?')[0])
        if not '.' in file_name: file_name += '.mp4'
        local_filepath = os.path.join(DOWNLOAD_PATH, f"{chat_id}_{file_name}")
        os.makedirs(DOWNLOAD_PATH, exist_ok=True)

        with requests.get(url, stream=True, timeout=FILE_TIMEOUT) as r:
            r.raise_for_status()
            total_size = int(r.headers.get('content-length', 0))

            def download_progress_callback(downloaded, total):
                current_time = time.time()
                if current_time - context.user_data.get('last_update_time', 0) > 2:
                    percentage = (downloaded / total) * 100 if total > 0 else 0
                    msg = (f"📥 **Downloading...**\n"
                           f"`{percentage:.1f}%` of `{human_readable_size(total)}`")
                    status_message.edit_text(msg, parse_mode='Markdown')
                    context.user_data['last_update_time'] = current_time

            downloaded_size = 0
            with open(local_filepath, 'wb') as f:
                for chunk in r.iter_content(chunk_size=1024 * 256):
                    f.write(chunk)
                    downloaded_size += len(chunk)
                    download_progress_callback(downloaded_size, total_size)

        # --- 2. THUMBNAIL ---
        status_message.edit_text("🖼 Download complete. Generating thumbnail...")
        thumbnail_filepath = generate_thumbnail(local_filepath)

        # --- 3. UPLOAD ---
        status_message.edit_text("📤 Preparing to upload...")
        
        def upload_progress_callback(uploaded, total):
            current_time = time.time()
            if current_time - context.user_data.get('last_update_time', 0) > 2:
                percentage = (uploaded / total) * 100 if total > 0 else 0
                msg = (f"📤 **Uploading to Telegram...**\n"
                       f"`{percentage:.1f}%` of `{human_readable_size(total)}`")
                status_message.edit_text(msg, parse_mode='Markdown')
                context.user_data['last_update_time'] = current_time

        with open(local_filepath, 'rb') as video_file:
            progress_wrapper = UploadProgressWrapper(video_file, upload_progress_callback)
            thumb_file = open(thumbnail_filepath, 'rb') if thumbnail_filepath else None
            try:
                context.bot.send_video(
                    chat_id=chat_id, video=progress_wrapper, caption=file_name,
                    supports_streaming=True, timeout=FILE_TIMEOUT, thumb=thumb_file
                )
            finally:
                if thumb_file: thumb_file.close()

        status_message.delete()

    except Exception as e:
        logger.error(f"An error occurred in process_link: {e}", exc_info=True)
        status_message.edit_text(f"❌ **An error occurred:**\n`{e}`")
    finally:
        # --- 4. CLEANUP ---
        if thumbnail_filepath and os.path.exists(thumbnail_filepath): os.remove(thumbnail_filepath)
        if local_filepath and os.path.exists(local_filepath): os.remove(local_filepath)

    return ConversationHandler.END

def cancel_command(update: Update, context: CallbackContext) -> int:
    """Cancels the current operation."""
    if 'status_message' in context.user_data:
        context.user_data['status_message'].edit_text("Operation cancelled by user.")
    else:
        update.message.reply_text("Operation cancelled.")
    
    # Note: This doesn't stop an in-progress download/upload,
    # but it stops the bot from proceeding to the next step.
    return ConversationHandler.END

def main():
    """Main function to set up and start the bot."""
    if BOT_TOKEN == 'YOUR_HTTP_API_TOKEN':
        print("!!! FATAL ERROR: Please replace 'YOUR_HTTP_API_TOKEN' with your bot token. !!!")
        return

    custom_bot = Bot(token=BOT_TOKEN, base_url=f"{LOCAL_API_URL}/bot")
    updater = Updater(bot=custom_bot, use_context=True)
    dispatcher = updater.dispatcher

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start_command)],
        states={
            RECEIVE_LINK: [MessageHandler(Filters.text & ~Filters.command, process_link)],
        },
        fallbacks=[CommandHandler('cancel', cancel_command)],
        per_user=True, # This makes the conversation specific to each user
        per_chat=True,
    )

    dispatcher.add_handler(conv_handler)
    dispatcher.add_handler(CommandHandler('help', help_command))

    updater.start_polling()
    print(f"Bot is running and connected to LOCAL server at {LOCAL_API_URL}...")
    updater.idle()

if __name__ == '__main__':
    main()
