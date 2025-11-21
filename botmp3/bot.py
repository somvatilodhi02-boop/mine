import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import config
from tasks import process_audio # Import the Celery task

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 ** MP3 Bot **\nSend a Youtube Video link to Convert Into MP3.")

async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text
    chat_id = update.effective_chat.id
    user_name = update.effective_user.first_name
    
    if "http" not in url:
        await update.message.reply_text("❌ Invalid link")
        return

    # 1. Send a placeholder message
    status_msg = await update.message.reply_text("⏳ ** Added to Queue... **")

    # 2. Send task to Celery (Redis)
    # .delay() puts it in the queue and returns immediately. The bot doesn't wait.
    process_audio.delay(url, chat_id, status_msg.message_id, user_name)

def main():
    app = Application.builder().token(config.BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link))
    
    print("🚀 Frontend Bot is listening...")
    app.run_polling()

if __name__ == "__main__":
    main()
