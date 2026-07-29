# === IMPORTS ===
import os
import logging
import threading
import concurrent.futures
from collections import defaultdict, deque

from dotenv import load_dotenv
load_dotenv()  # reads .env automatically — no need to `export` manually

from flask import Flask, request, send_file, abort
import telebot

telebot.logger.setLevel(logging.DEBUG)  # prints every update received — helps debugging

from agent import run_agent
from logger import LOG_PATH, ensure_log_file

# === CONFIG ===
BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")  # e.g. https://your-app.onrender.com
WEBHOOK_PATH = f"/webhook/{BOT_TOKEN}"

bot = telebot.TeleBot(BOT_TOKEN, threaded=True)
app = Flask(__name__)
ensure_log_file()

# in-memory per-chat conversation history (for multi-turn questions)
# NOTE: resets on restart. Good enough for short multi-turn eval sequences.
chat_history = defaultdict(lambda: deque(maxlen=10))
history_lock = threading.Lock()
agent_pool = concurrent.futures.ThreadPoolExecutor(max_workers=4)
AGENT_TIMEOUT_SECONDS = 60


# === TELEGRAM MESSAGE HANDLER ===
@bot.message_handler(func=lambda m: True, content_types=["text"])
def handle_message(message):
    chat_id = message.chat.id
    user_text = message.text
    print(f"[RECEIVED] chat_id={chat_id} text={user_text!r}")

    with history_lock:
        chat_history[chat_id].append({"role": "user", "content": user_text})
        history = list(chat_history[chat_id])

    try:
        reply_text = run_agent(history, chat_id)
        if not reply_text or not reply_text.strip():
            raise ValueError("agent returned an empty reply")
        print(f"[AGENT REPLY] {reply_text!r}")
    except Exception as e:
        import traceback
        traceback.print_exc()
        # Never let the bot go silent — always reply with something,
        # since a non-reply is scored the same as a wrong answer.
        reply_text = '{"answer": null, "log_url": "%s", "error": "%s"}' % (
            os.environ.get("PUBLIC_LOG_URL", ""),
            str(e).replace('"', "'"),
        )

    with history_lock:
        chat_history[chat_id].append({"role": "assistant", "content": reply_text})

    bot.send_message(chat_id, reply_text)
    print(f"[SENT] chat_id={chat_id}")


# === FLASK ROUTES ===
@app.route(WEBHOOK_PATH, methods=["POST"])
def telegram_webhook():
    update = telebot.types.Update.de_json(request.get_json(force=True))
    bot.process_new_updates([update])
    return "OK", 200


@app.route("/run.jsonl", methods=["GET"])
def serve_log():
    if not os.path.exists(LOG_PATH):
        abort(404)
    return send_file(LOG_PATH, mimetype="text/plain")


@app.route("/", methods=["GET"])
def health():
    return "Data analyst bot is running.", 200


# === WEBHOOK SETUP (run once on boot) ===
def setup_webhook():
    bot.remove_webhook()
    if WEBHOOK_URL:
        bot.set_webhook(url=f"{WEBHOOK_URL}{WEBHOOK_PATH}")


setup_webhook()

if __name__ == "__main__":
    # local testing only — uses polling instead of webhook
    bot.remove_webhook()
    app_thread = threading.Thread(
        target=lambda: app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
    )
    app_thread.start()
    bot.infinity_polling()
