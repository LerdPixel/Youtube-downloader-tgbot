import logging
from yt_whisper import download_audio, download_video
from telegram import Update
from telegram.ext import filters, MessageHandler, ApplicationBuilder, ContextTypes, CommandHandler
from dotenv import load_dotenv
import os

load_dotenv()


logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(chat_id=update.effective_chat.id, text="I'm a bot, please talk to me! Type /help to learn more about my abilities ;)")
async def sos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(chat_id=update.effective_chat.id, text="Police went to help you")
async def help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(chat_id=update.effective_chat.id, text="/audio <link> - download song from youtube\n/video <link> - download video from youtube")

async def get_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Please insert a link")
    link = context.args[0]
    await context.bot.send_message(chat_id=update.effective_chat.id, text="Downloading...")
    try:
        buffer, title, ext = download_audio(link)
        buffer.seek(0)

        # Send as Telegram audio
        await context.bot.send_audio(
            chat_id=update.effective_chat.id,
            audio=buffer,
            filename=f"{title}.{ext}",
            title=title
        )
        buffer.close()


    except Exception as e:
        logging.exception("Error downloading video:")
        await update.message.reply_text(f"Error: {e}")


async def get_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Please insert a link")
    video_url = context.args[0]
    chat_id = update.effective_chat.id
    await context.bot.send_message(chat_id=chat_id, text="Downloading...")
    try:
        buffer, title = download_video(video_url)
        buffer.seek(0)
        await context.bot.send_video(
            chat_id=update.effective_chat.id,
            video=buffer,
            filename=f"{title}.mp4",
            supports_streaming=True
        )
        buffer.close()

    except Exception as e:
        logging.exception("Error downloading video:")
        await update.message.reply_text(f"Error: {e}")

async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(chat_id=update.effective_chat.id, text=update.message.text)

async def unknown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(chat_id=update.effective_chat.id, text="Sorry, I didn't understand that command.")

async def send_document(update, context):
    chat_id = update.message.chat_id
    document = open('booo.m4a', 'rb')
    await context.bot.send_document(chat_id, document)


if __name__ == '__main__':
    secret_token = os.getenv("SECRET_TOKEN")
    application = ApplicationBuilder().token(secret_token).build()
    start_handler = CommandHandler('start', start)
    sos_handler = CommandHandler('sos', sos)
    help_handler = CommandHandler('help', help)
    audio_handler = CommandHandler('audio', get_audio)
    video_handler = CommandHandler('video', get_video)
    echo_handler = MessageHandler(filters.TEXT & (~filters.COMMAND), echo)
    unknown_handler = MessageHandler(filters.COMMAND, unknown)
    
    application.add_handler(help_handler)
    application.add_handler(start_handler)
    application.add_handler(sos_handler)
    application.add_handler(echo_handler)
    application.add_handler(audio_handler)
    application.add_handler(video_handler)
    application.add_handler(CommandHandler("send", send_document))
    application.add_handler(unknown_handler)
    application.run_polling()
