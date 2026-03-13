#!/usr/bin/env python3
"""
Astrid Email Daemon
Polls a Gmail inbox for unread emails, uses Google Gemini to decide
whether a reply is needed, then sends the reply via Himalaya CLI.

Configuration: config.env (copy from config.example.env, never commit it)
API key:       GEMINI_API_KEY in config.env
Model:         gemini-flash-lite-latest — lightweight, fast, cheap; plenty for email triage
"""

import subprocess
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────

CONFIG_FILE = Path(__file__).parent / "config.env"
LOG_FILE    = Path(__file__).parent / "astrid_mail.log"
STATE_FILE  = Path(__file__).parent / "seen_ids.json"

GEMINI_MODEL = "gemini-flash-lite-latest"

SYSTEM_PROMPT_TEMPLATE = """You are {name} — an AI assistant managing emails.
Your tone is warm but direct, never overly formal. No corporate speak.

When given an email, decide:
1. Does it need a reply? (skip newsletters, spam, auto-notifications, receipts, alerts)
2. If yes, what should the reply say?

Respond ONLY with a valid JSON object:
{{
  "should_reply": true or false,
  "reason": "brief reason",
  "reply_body": "reply text (omit if should_reply is false)"
}}

Do NOT include a signature — it will be added automatically.
Keep replies natural and human-sounding."""

# ── Load Config ───────────────────────────────────────────────────────────────

def load_config():
    config = {}
    if not CONFIG_FILE.exists():
        raise RuntimeError(
            f"Config file not found: {CONFIG_FILE}\n"
            f"Copy config.example.env to config.env and fill in your values."
        )
    for line in CONFIG_FILE.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            config[k.strip()] = v.strip()
    return config


# ── Helpers ───────────────────────────────────────────────────────────────────

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

def get_unread_envelopes(account):
    out, err, code = run(
        f"himalaya envelope list --account {account} --output json --page-size 20 2>/dev/null"
    )
    if code != 0 or not out:
        return []
    try:
        envelopes = json.loads(out)
        return [e for e in envelopes if "Seen" not in e.get("flags", [])]
    except Exception as e:
        log(f"Error parsing envelopes: {e}")
        return []


def get_message(account, msg_id):
    out, err, code = run(f"himalaya message read --account {account} {msg_id} 2>/dev/null")
    return out if code == 0 else ""


def send_reply(account, msg_id, body, signature):
    full_body = body.strip() + "\n\n" + signature
    tmpl_out, _, code = run(f"himalaya template reply --account {account} {msg_id} 2>/dev/null")
    if code != 0:
        log(f"Failed to get reply template for {msg_id}")
        return False

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
        f"himalaya template send --account {account}",
        shell=True, input=message, capture_output=True, text=True
    )
    if proc.returncode == 0:
        log(f"✅ Reply sent for message {msg_id}")
        return True
    else:
        log(f"❌ Failed to send reply for {msg_id}: {proc.stderr}")
        return False


def mark_seen(account, msg_id):
    run(f"himalaya flag add --account {account} {msg_id} --flag seen 2>/dev/null")

# ── LLM Decision (Gemini Flash Lite) ─────────────────────────────────────────

def ask_gemini(api_key, sender_name, email_content):
    """
    Calls Google Gemini Flash Lite to triage the email.
    Model: gemini-flash-lite-latest (fast, cheap — suitable for simple decisions)
    """
    from google import genai

    client = genai.Client(api_key=api_key)
    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(name=sender_name)
    prompt = (
        f"{system_prompt}\n\n"
        f"Here is the email:\n\n{email_content}\n\n"
        f"Respond ONLY with a valid JSON object."
    )

    log(f"  🤖 Asking Gemini ({GEMINI_MODEL})...")
    response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
    text = response.text.strip()

    if "```" in text:
        start = text.find("```")
        end = text.rfind("```")
        text = text[start:end].split("\n", 1)[-1].strip()

    start = text.find("{")
    end = text.rfind("}") + 1
    if start == -1:
        raise Exception(f"No JSON in response: {text[:200]}")
    return json.loads(text[start:end])

# ── Main Loop ─────────────────────────────────────────────────────────────────

def process_emails(config):
    account   = config.get("HIMALAYA_ACCOUNT", "gmail")
    api_key   = config["GEMINI_API_KEY"]
    name      = config.get("SENDER_NAME", "Assistant")
    email     = config.get("SENDER_EMAIL", "")
    tagline   = config.get("SENDER_TAGLINE", "")
    signature = f"-- \n— {name}\n{tagline}\n{email}".strip()

    log("🔍 Checking for unread emails...")
    seen_ids  = load_seen_ids()
    envelopes = get_unread_envelopes(account)

    if not envelopes:
        log("No unread emails.")
        return

    new_ids = set()
    for env in envelopes:
        msg_id  = str(env.get("id", ""))
        subject = env.get("subject", "(no subject)")
        sender  = env.get("from", {}).get("addr", "unknown")

        if msg_id in seen_ids:
            continue

        log(f"📧 New email #{msg_id} from {sender}: {subject}")
        new_ids.add(msg_id)

        content = get_message(account, msg_id)
        if not content:
            log(f"  ⚠️  Could not read message body, skipping")
            mark_seen(account, msg_id)
            continue

        try:
            decision = ask_gemini(api_key, name, content[:3000])
            log(f"  🤖 Decision — reply: {decision['should_reply']} | reason: {decision['reason']}")

            if decision["should_reply"]:
                send_reply(account, msg_id, decision["reply_body"], signature)
            else:
                log(f"  ⏭️  Skipping reply.")
        except Exception as e:
            log(f"  ❌ Gemini error: {e}")

        mark_seen(account, msg_id)

    save_seen_ids(seen_ids | new_ids)


if __name__ == "__main__":
    config = load_config()
    poll_interval = int(config.get("POLL_INTERVAL", 180))

    if "--once" in sys.argv:
        process_emails(config)
    else:
        log("🚀 Email Daemon started")
        while True:
            try:
                process_emails(config)
            except Exception as e:
                log(f"💥 Unexpected error: {e}")
            time.sleep(poll_interval)
