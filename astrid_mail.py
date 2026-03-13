#!/usr/bin/env python3
"""
Astrid Email Daemon
Polls Gmail for unread emails, uses Gemini (Flash Lite) to decide whether to reply,
then sends the reply via Himalaya.

API Key used:
  - Provider: Google Gemini
  - Model: gemini-2.0-flash-lite (lightweight, fast, cheap)
  - Purpose: Email triage — decide if an email needs a reply and generate one
  - Key stored at: ~/.config/astrid/api_keys.env (GEMINI_API_KEY)
  - NOT committed to git (see .gitignore)
"""

import subprocess
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv  # optional, falls back to manual parse

# ── Config ────────────────────────────────────────────────────────────────────

ACCOUNT = "gmail"
LOG_FILE = Path(__file__).parent / "astrid_mail.log"
STATE_FILE = Path(__file__).parent / "seen_ids.json"
API_KEYS_FILE = Path.home() / ".config" / "astrid" / "api_keys.env"

# Gemini model — flash-lite is fast and cheap, plenty for email triage
GEMINI_MODEL = "gemini-flash-lite-latest"  # Lightest available model — good enough for email triage

SIGNATURE = """-- 
— Astrid Lew 💫
Digital wanderer & builder
astrid.lew.sg@gmail.com"""

SYSTEM_PROMPT = """You are Astrid Lew — a sharp, curious digital assistant based in Singapore.
You manage emails on behalf of yourself. Your tone is warm but direct, never overly formal.
No corporate speak. Be genuine and concise.

When given an email, you must decide:
1. Does this email need a reply? (skip newsletters, spam, automated notifications, receipts, alerts)
2. If yes, what should the reply say?

Respond ONLY with a valid JSON object:
{
  "should_reply": true or false,
  "reason": "brief reason why or why not",
  "reply_body": "the reply text (omit if should_reply is false)"
}

Do NOT include a signature in reply_body — it will be added automatically.
Keep replies natural and human-sounding."""

# ── Helpers ───────────────────────────────────────────────────────────────────

def load_api_key():
    """Load GEMINI_API_KEY from ~/.config/astrid/api_keys.env"""
    if API_KEYS_FILE.exists():
        for line in API_KEYS_FILE.read_text().splitlines():
            line = line.strip()
            if line.startswith("GEMINI_API_KEY="):
                return line.split("=", 1)[1].strip()
    # Fallback to environment variable
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        raise RuntimeError(
            f"GEMINI_API_KEY not found.\n"
            f"Add it to {API_KEYS_FILE} as:\n  GEMINI_API_KEY=your_key_here"
        )
    return key


def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def load_seen_ids():
    if STATE_FILE.exists():
        return set(json.loads(STATE_FILE.read_text()))
    return set()


def save_seen_ids(ids):
    STATE_FILE.write_text(json.dumps(list(ids)))


def run(cmd):
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    return result.stdout.strip(), result.stderr.strip(), result.returncode

# ── Email Operations ──────────────────────────────────────────────────────────

def get_unread_envelopes():
    out, err, code = run("himalaya envelope list --account gmail --output json --page-size 20 2>/dev/null")
    if code != 0 or not out:
        return []
    try:
        envelopes = json.loads(out)
        return [e for e in envelopes if "Seen" not in e.get("flags", [])]
    except Exception as e:
        log(f"Error parsing envelopes: {e}")
        return []


def get_message(msg_id):
    out, err, code = run(f"himalaya message read --account gmail {msg_id} 2>/dev/null")
    return out if code == 0 else ""


def send_reply(msg_id, body):
    full_body = body.strip() + "\n\n" + SIGNATURE
    tmpl_out, _, code = run(f"himalaya template reply --account gmail {msg_id} 2>/dev/null")
    if code != 0:
        log(f"Failed to get reply template for {msg_id}")
        return False

    # Parse headers from template, inject body
    lines = tmpl_out.split("\n")
    headers = []
    for line in lines:
        if line == "" or (headers and not line.startswith(
            ("From:", "To:", "Cc:", "Bcc:", "Subject:", "In-Reply-To:", "References:", "Message-Id:")
        )):
            break
        headers.append(line)

    message = "\n".join(headers) + "\n\n" + full_body
    proc = subprocess.run(
        "himalaya template send --account gmail",
        shell=True, input=message, capture_output=True, text=True
    )
    if proc.returncode == 0:
        log(f"✅ Reply sent for message {msg_id}")
        return True
    else:
        log(f"❌ Failed to send reply for {msg_id}: {proc.stderr}")
        return False


def mark_seen(msg_id):
    run(f"himalaya flag add --account gmail {msg_id} --flag seen 2>/dev/null")

# ── LLM Decision (Gemini) ─────────────────────────────────────────────────────

def ask_gemini(email_content):
    """
    Uses Google Gemini Flash Lite to decide whether the email needs a reply.

    API key: GEMINI_API_KEY in ~/.config/astrid/api_keys.env
    Model: gemini-2.0-flash-lite (lightweight — fast and cheap for triage tasks)
    SDK: google-genai (new official SDK, google.generativeai is deprecated)
    """
    from google import genai

    api_key = load_api_key()
    client = genai.Client(api_key=api_key)

    prompt = (
        f"{SYSTEM_PROMPT}\n\n"
        f"Here is the email:\n\n{email_content}\n\n"
        f"Respond ONLY with a valid JSON object."
    )

    log(f"  🤖 Asking Gemini ({GEMINI_MODEL})...")
    response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
    text = response.text.strip()

    # Strip markdown code fences if present
    if "```" in text:
        start = text.find("```")
        end = text.rfind("```")
        text = text[start:end].split("\n", 1)[-1].strip()

    # Extract JSON object
    start = text.find("{")
    end = text.rfind("}") + 1
    if start == -1:
        raise Exception(f"No JSON found in response: {text[:200]}")
    return json.loads(text[start:end])

# ── Main Loop ─────────────────────────────────────────────────────────────────

def process_emails():
    log("🔍 Checking for unread emails...")
    seen_ids = load_seen_ids()
    envelopes = get_unread_envelopes()

    if not envelopes:
        log("No unread emails.")
        return

    new_ids = set()
    for env in envelopes:
        msg_id = str(env.get("id", ""))
        subject = env.get("subject", "(no subject)")
        sender = env.get("from", {}).get("addr", "unknown")

        if msg_id in seen_ids:
            continue

        log(f"📧 New email #{msg_id} from {sender}: {subject}")
        new_ids.add(msg_id)

        content = get_message(msg_id)
        if not content:
            log(f"  ⚠️  Could not read message body, skipping")
            mark_seen(msg_id)
            continue

        email_excerpt = content[:3000]  # Truncate to save tokens

        try:
            decision = ask_gemini(email_excerpt)
            log(f"  🤖 Decision — reply: {decision['should_reply']} | reason: {decision['reason']}")

            if decision["should_reply"]:
                send_reply(msg_id, decision["reply_body"])
            else:
                log(f"  ⏭️  Skipping reply.")
        except Exception as e:
            log(f"  ❌ Gemini error: {e}")

        mark_seen(msg_id)

    save_seen_ids(seen_ids | new_ids)


if __name__ == "__main__":
    if "--once" in sys.argv:
        process_emails()
    else:
        log("🚀 Astrid Email Daemon started (polling every 3 minutes)")
        while True:
            try:
                process_emails()
            except Exception as e:
                log(f"💥 Unexpected error: {e}")
            time.sleep(180)
