# === IMPORTS ===
import os
import json
import threading
from datetime import datetime, timezone

LOG_PATH = os.path.join(os.path.dirname(__file__), "logs", "run.jsonl")
_lock = threading.Lock()


def ensure_log_file():
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    if not os.path.exists(LOG_PATH):
        open(LOG_PATH, "a").close()


def log_run(chat_id, question, answer, tool_calls=None):
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "chat_id": chat_id,
        "question": question,
        "answer": answer,
        "tool_calls": tool_calls or [],
    }
    ensure_log_file()
    with _lock:
        with open(LOG_PATH, "a") as f:
            f.write(json.dumps(entry) + "\n")
