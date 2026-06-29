import asyncio
import logging
import os
import time

from dotenv import load_dotenv
from telegram import Update
from telegram.error import BadRequest
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, MessageHandler, filters

from yt_whisper import download_audio, download_video

load_dotenv()

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

BAR_WIDTH = 20
PROGRESS_UPDATE_INTERVAL = 0.8


def _format_progress_text(downloaded: int, total: int | None, phase: str) -> str:
    mb_done = downloaded / (1024 * 1024)

    if total and total > 0:
        pct = min(100, downloaded * 100 // total)
        filled = pct * BAR_WIDTH // 100
        bar = "█" * filled + "░" * (BAR_WIDTH - filled)
        mb_total = total / (1024 * 1024)
        return f"⏳ {phase}\n[{bar}] {pct}% · {mb_done:.1f}/{mb_total:.1f} MB"

    filled = min(BAR_WIDTH, downloaded // (512 * 1024))
    bar = "█" * filled + "░" * (BAR_WIDTH - filled)
    return f"⏳ {phase}\n[{bar}] {mb_done:.1f} MB downloaded"


class ProgressReporter:
    def __init__(self, bot, chat_id: int, message_id: int, loop: asyncio.AbstractEventLoop):
        self.bot = bot
        self.chat_id = chat_id
        self.message_id = message_id
        self.loop = loop
        self.last_update = 0.0
        self.last_text = ""

    def update(self, downloaded: int, total: int | None, phase: str) -> None:
        text = _format_progress_text(downloaded, total, phase)
        if text == self.last_text:
            return

        now = time.monotonic()
        if now - self.last_update < PROGRESS_UPDATE_INTERVAL and total and downloaded < total:
            return

        self.last_update = now
        self.last_text = text
        asyncio.run_coroutine_threadsafe(self._edit(text), self.loop)

    async def _edit(self, text: str) -> None:
        try:
            await self.bot.edit_message_text(
                chat_id=self.chat_id,
                message_id=self.message_id,
                text=text,
            )
        except BadRequest as exc:
            if "Message is not modified" not in str(exc):
                logging.debug("Failed to update progress message: %s", exc)

    async def delete(self) -> None:
        try:
            await self.bot.delete_message(chat_id=self.chat_id, message_id=self.message_id)
        except BadRequest:
            pass


async def _run_download(update: Update, context: ContextTypes.DEFAULT_TYPE, download_fn, url: str):
    chat_id = update.effective_chat.id
    loop = asyncio.get_running_loop()

    status_msg = await context.bot.send_message(
        chat_id=chat_id,
        text=_format_progress_text(0, None, "Fetching info"),
    )
    reporter = ProgressReporter(context.bot, chat_id, status_msg.message_id, loop)

    def on_progress(downloaded: int, total: int | None, phase: str) -> None:
        reporter.update(downloaded, total, phase)

    try:
        result = await asyncio.to_thread(download_fn, url, progress_callback=on_progress)
        await reporter.delete()
        return result
    except Exception as exc:
        await status_msg.edit_text(f"❌ Error: {exc}")
        raise


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(chat_id=update.effective_chat.id, text="I'm a bot, please talk to me! Type /help to learn more about my abilities ;)")


async def sos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(chat_id=update.effective_chat.id, text="Police went to help you")


async def help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="/audio <link> - download song from youtube\n/video <link> - download video from youtube",
    )


async def get_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Please insert a link")
        return

    link = context.args[0]
    try:
        buffer, title, ext = await _run_download(update, context, download_audio, link)
        buffer.seek(0)
        await context.bot.send_audio(
            chat_id=update.effective_chat.id,
            audio=buffer,
            filename=f"{title}.{ext}",
            title=title,
        )
        buffer.close()
    except Exception:
        logging.exception("Error downloading audio:")


async def get_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Please insert a link")
        return

    video_url = context.args[0]
    try:
        buffer, title, ext = await _run_download(update, context, download_video, video_url)
        buffer.seek(0)
        await context.bot.send_video(
            chat_id=update.effective_chat.id,
            video=buffer,
            filename=f"{title}.{ext}",
            supports_streaming=True,
        )
        buffer.close()
    except Exception:
        logging.exception("Error downloading video:")


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
