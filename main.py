import os
import logging
import openai
import uvicorn

from fastapi import FastAPI, Request, HTTPException
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters
)

# ---------------------------------
#  Конфигурация окружения
# ---------------------------------
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "secret")
TRANSCRIBE_MODE = os.environ.get("TRANSCRIBE_MODE", "gpt-4o-mini-transcribe")
ALLOWED_CHATS = os.environ.get("ALLOWED_CHATS", "")

openai.api_key = OPENAI_API_KEY

# Telegram bot
application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

# FastAPI app
app = FastAPI()

# ---------------------------------
#  Хелперы
# ---------------------------------
def allowed_chat(chat_id: int) -> bool:
    if not ALLOWED_CHATS:
        return True
    ids = [int(x.strip()) for x in ALLOWED_CHATS.split(",")]
    return chat_id in ids


def split_text(text, limit=4000):
    """Делит длинный текст на куски для Telegram"""
    chunks, cur = [], ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > limit:
            chunks.append(cur)
            cur = line
        else:
            cur += "\n" + line
    if cur:
        chunks.append(cur)
    return chunks

# ---------------------------------
#  Хэндлеры Telegram
# ---------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привет! Я бот для транскрибирования Reels 🎬")


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Отправь мне голосовое или видео, и я его расшифрую!")


async def transcribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed_chat(update.effective_chat.id):
        await update.message.reply_text("⛔ У тебя нет доступа к этому боту")
        return

    file = await update.message.voice.get_file() if update.message.voice else None
    if not file:
        await update.message.reply_text("Пришли голосовое сообщение 🎤")
        return

    file_path = "temp.ogg"
    await file.download_to_drive(file_path)

    try:
        with open(file_path, "rb") as f:
            transcript = openai.audio.transcriptions.create(
                model=TRANSCRIBE_MODE,
                file=f
            )

        text = transcript.text
        chunks = split_text(text)
        for chunk in chunks:
            await update.message.reply_text(chunk)

    except Exception as e:
        logging.error(f"Ошибка транскрибации: {e}")
        await update.message.reply_text("Произошла ошибка при транскрибации.")


# ---------------------------------
#  Роуты FastAPI
# ---------------------------------
@app.get("/")
async def root():
    return {"status": "ok", "message": "Insta Transcriber Bot is running!"}


@app.post("/webhook")
async def webhook(request: Request):
    if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="Invalid secret")

    data = await request.json()
    update = Update.de_json(data, application.bot)
    await application.initialize()
    await application.process_update(update)
    return {"ok": True}


# ---------------------------------
#  Регистрация хэндлеров
# ---------------------------------
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("help", help_cmd))
application.add_handler(MessageHandler(filters.VOICE, transcribe))


# ---------------------------------
#  Точка входа (Cloud Run)
# ---------------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run("main:app", host="0.0.0.0", port=port)

