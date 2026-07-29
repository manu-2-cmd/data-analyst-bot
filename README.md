# Data Analyst Telegram Bot

An LLM agent that answers data-analysis questions (MOSPI and similar public
datasets) over Telegram and replies with a single JSON object.

## How it works
- `app.py` — one Flask server that (a) receives Telegram messages via webhook
  and (b) serves the run log publicly at `/run.jsonl`.
- `agent.py` — calls **aipipe.org** (OpenAI-compatible proxy, IITM's free LLM
  gateway) with two tools: `web_search` (free DuckDuckGo search, no key
  needed) and `python_exec` (download CSVs, compute answers with pandas).
  Loops until the model returns the final JSON-only answer.
- `logger.py` — appends one JSON line per run to `logs/run.jsonl`.

## Setup (local)

1. `python -m venv venv && source venv/bin/activate` (Windows: `venv\Scripts\activate`)
2. `pip install -r requirements.txt`
3. Copy `.env.example` to `.env` and fill in:
   - `TELEGRAM_BOT_TOKEN` — from @BotFather
   - `AIPIPE_TOKEN` — from aipipe.org (log in with your IITM Google account,
     copy the token shown)
   - `WEBHOOK_URL` — leave blank for local testing
   - `PUBLIC_LOG_URL` — leave as-is for local testing
4. Load env vars and run locally in **polling** mode (no public URL needed):
   ```bash
   export $(cat .env | xargs)   # or use python-dotenv
   python app.py
   ```
   This starts polling — message your bot on Telegram and check the reply.

## Test with the official eval repo

```bash
git clone https://github.com/Jivraj-18/tds-p1-t2-2026-telegram-bot
cd tds-p1-t2-2026-telegram-bot
# add your bot username + a few test questions to evals/questions.json
# follow that repo's README to run the eval against your bot
```

## Deploy (Render.com — free, keeps a stable public URL)

1. Push this folder to a **public GitHub repo**.
2. On Render.com: New → Web Service → connect your repo.
3. Build command: `pip install -r requirements.txt`
   Start command: `gunicorn app:app --bind 0.0.0.0:$PORT --timeout 120`
4. Add environment variables (same as `.env`, but set:
   - `WEBHOOK_URL = https://<your-service-name>.onrender.com`
   - `PUBLIC_LOG_URL = https://<your-service-name>.onrender.com/run.jsonl`
5. Deploy. On boot, `app.py` automatically calls `setup_webhook()`, which
   registers `WEBHOOK_URL + /webhook/<token>` with Telegram — no manual
   `setWebhook` call needed.
6. Verify:
   ```bash
   curl https://<your-service-name>.onrender.com/
   wget https://<your-service-name>.onrender.com/run.jsonl
   ```
7. Message your bot on Telegram — you should get a JSON reply.

### Keeping it awake
Render's free web services sleep after ~15 min idle and wake on the next
request (a few seconds delay on first message after sleep — acceptable for
grading, but if you want zero cold-start, add a free uptime-pinger like
UptimeRobot hitting `/` every 5 minutes, or use a paid/always-on tier.

## Notes
- Multi-turn: the bot keeps the last 10 messages per chat in memory and
  answers the latest one using that context. This resets on redeploy/restart
  — fine for short eval sequences sent close together.
- The log file lives on Render's ephemeral disk — it persists for the life of
  the running instance (fine for grading) but resets on redeploy. For a more
  durable log, swap `logger.py` to write to S3/GCS/a Gist instead.
