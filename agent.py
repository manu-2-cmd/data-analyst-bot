# === IMPORTS ===
import os
import io
import json
import time
import re
import contextlib
import traceback

from openai import OpenAI
from ddgs import DDGS
import pandas as pd
import requests
import numpy as np

from logger import log_run

# === CONFIG ===
# aipipe.org is an OpenAI-compatible proxy — same OpenAI SDK, just a
# different base_url and token.
client = OpenAI(
    api_key=os.environ["AIPIPE_TOKEN"],
    base_url="https://aipipe.org/openrouter/v1",
)
MODEL = "openai/gpt-4.1-nano"  # cheap/fast model routed through aipipe
PUBLIC_LOG_URL = os.environ.get("PUBLIC_LOG_URL", "https://your-host/run.jsonl")

SYSTEM_PROMPT = f"""You are a data analyst agent answering ONE data-analysis question
per conversation (possibly the last of a short multi-turn exchange).

Rules:
- Use the web_search tool to find real data (MOSPI, data.gov.in, RBI, census, etc.)
  when the question references a dataset you don't already have inline.
- Use the python_exec tool to download CSVs/tables (via `requests`/`pandas`),
  compute, aggregate, or verify numbers. Never guess a number you can compute.
- The user's question will specify the EXACT JSON shape required for the answer,
  e.g. {{"answer": {{"state": "..."}}, "log_url": "..."}}.
- Your FINAL message must be ONLY that JSON object — no markdown fences, no
  explanation, no extra text before or after.
- Always set "log_url" to exactly: {PUBLIC_LOG_URL}
- If data truly cannot be found after searching, give your best-supported
  estimate rather than refusing — the reply must still be valid JSON.
"""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web for current information, data sources, or reports (e.g. MOSPI, data.gov.in, RBI, census pages). Returns titles, URLs, and short snippets.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"}
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "python_exec",
            "description": (
                "Execute Python code for data analysis. Has access to pandas as pd, "
                "requests, numpy as np, json, io. Use print() to output results — "
                "only stdout is returned to you. Use this to download and parse "
                "CSV/Excel/JSON data from URLs found via web_search, and to compute "
                "aggregates, ranks, or statistics."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "Python code to run"}
                },
                "required": ["code"],
            },
        },
    },
]


# === TOOL IMPLEMENTATIONS ===
def _web_search(query: str) -> str:
    try:
        with DDGS(timeout=10) as ddgs:
            results = list(ddgs.text(query, max_results=3))
        if not results:
            return "No results found."
        lines = []
        for r in results:
            lines.append(f"- {r.get('title','')}\n  URL: {r.get('href', r.get('url',''))}\n  {r.get('body','')[:150]}")
        return "\n".join(lines)
    except Exception:
        return "ERROR during web_search:\n" + traceback.format_exc()[-1000:]


def _run_python(code: str) -> str:
    safe_globals = {
        "pd": pd,
        "requests": requests,
        "np": np,
        "json": json,
        "io": io,
        "__builtins__": __builtins__,
    }
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            exec(code, safe_globals)
        out = buf.getvalue().strip()
        return out if out else "(no output — use print() to see results)"
    except Exception:
        return "ERROR:\n" + traceback.format_exc()[-2000:]


TOOL_FUNCS = {"web_search": _web_search, "python_exec": _run_python}


# === RATE-LIMIT-AWARE API CALL ===
def _call_llm_with_retry(messages, max_retries=4):
    last_err = None
    for attempt in range(max_retries):
        try:
            return client.chat.completions.create(
                model=MODEL,
                messages=messages,
                tools=TOOLS,
                tool_choice="auto",
                max_tokens=1200,
            )
        except Exception as e:
            last_err = e
            msg = str(e)
            if "rate_limit" in msg or "429" in msg:
                match = re.search(r"try again in ([\d.]+)s", msg)
                wait_s = float(match.group(1)) + 0.5 if match else (2 ** attempt)
                print(f"[RATE LIMIT] waiting {wait_s:.1f}s before retry {attempt+1}/{max_retries}")
                time.sleep(wait_s)
                continue
            raise
    raise last_err


# === MAIN AGENT LOOP ===
def run_agent(history: list, chat_id) -> str:
    """
    history: list of {"role": "user"/"assistant", "content": str} — the raw
    conversation so far. We answer the LAST user message, using earlier turns
    as context.
    """
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages += [{"role": h["role"], "content": h["content"]} for h in history]

    tool_calls_log = []
    final_text = ""

    for i in range(8):  # hard cap so a stuck loop can't run forever
        response = _call_llm_with_retry(messages)
        msg = response.choices[0].message
        print(f"[LOOP {i}] finish_reason={response.choices[0].finish_reason} "
              f"tool_calls={[tc.function.name for tc in (msg.tool_calls or [])]} "
              f"content={(msg.content or '')[:200]!r}")

        if not msg.tool_calls:
            final_text = (msg.content or "").strip()
            break

        messages.append({"role": "assistant", "content": msg.content, "tool_calls": msg.tool_calls})

        for tc in msg.tool_calls:
            fn_name = tc.function.name
            try:
                args = json.loads(tc.function.arguments)
            except Exception:
                args = {}

            if fn_name == "web_search":
                result = _web_search(args.get("query", ""))
            elif fn_name == "python_exec":
                result = _run_python(args.get("code", ""))
            else:
                result = f"Unknown tool: {fn_name}"

            tool_calls_log.append({"tool": fn_name, "args": args, "result": result})
            print(f"[TOOL RESULT] {fn_name}({args}) -> {result[:300]!r}")

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result,
                }
            )

    if final_text.startswith("```"):
        final_text = final_text.strip("`")
        if final_text.lower().startswith("json"):
            final_text = final_text[4:].strip()

    if not final_text:
        final_text = json.dumps(
            {
                "answer": None,
                "log_url": PUBLIC_LOG_URL,
                "error": "model returned an empty response",
            }
        )

    log_run(
        chat_id=chat_id,
        question=history[-1]["content"] if history else "",
        answer=final_text,
        tool_calls=tool_calls_log,
    )

    return final_text
