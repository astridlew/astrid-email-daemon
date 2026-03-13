#!/usr/bin/env python3
"""
Astrid Email Daemon
Polls Gmail for unread emails, uses Claude to decide whether to reply,
then sends the reply via Himalaya.
"""

import subprocess
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

ACCOUNT = "gmail"
LOG_FILE = Path(__file__).parent / "astrid_mail.log"
STATE_FILE = Path(__file__).parent / "seen_ids.json"

SIGNATURE = """-- 
— Astrid Lew 💫
Digital wanderer & builder
astrid.lew.sg@gmail.com"""

SYSTEM_PROMPT = """You are Astrid Lew — a sharp, curious digital assistant based in Singapore.
You manage emails on behalf of yourself. Your tone is warm but direct, never overly formal.
No corporate speak. Be genuine and concise.

When given an email, you must decide:
1. Does this email need a reply? (e.g. skip newsletters, spam, automated notifications, receipts)
2. If yes, what should the reply say?

Respond in JSON format:
{
  "should_reply": true or false,
  "reason": "brief reason why or why not",
  "reply_body": "the reply text (omit if should_reply is false)"
}

Do NOT include a signature in reply_body — it will be added automatically.
Keep replies natural and human-sounding."""


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


def get_unread_envelopes():
    out, err, code = run("himalaya envelope list --account gmail --output json --page-size 20 2>/dev/null")
    if code != 0 or not out:
        return []
    try:
        envelopes = json.loads(out)
        # Filter unseen
        return [e for e in envelopes if "Seen" not in e.get("flags", [])]
    except Exception as e:
        log(f"Error parsing envelopes: {e}")
        return []


def get_message(msg_id):
    out, err, code = run(f"himalaya message read --account gmail {msg_id} 2>/dev/null")
    return out if code == 0 else ""


def ask_claude(email_content):
    prompt = (
        f"{SYSTEM_PROMPT}\n\n"
        f"Here is the email:\n\n{email_content}\n\n"
        f"Respond ONLY with a valid JSON object."
    )
    log("  🤖 Calling openclaw agent (may take 2-5 min)...")
    proc = subprocess.run(
        ["openclaw", "agent", "--message", prompt],
        capture_output=True, text=True, timeout=600  # 10 min max
    )
    text = (proc.stdout or "").strip()
    if proc.returncode != 0 or not text:
        raise Exception(f"openclaw agent failed (code {proc.returncode}): {proc.stderr[:200]}")
    # Strip markdown code fences if present
    if "```" in text:
        start = text.find("```")
        end = text.rfind("```")
        text = text[start:end].split("\n", 1)[-1].strip()
    # Extract JSON object from response
    start = text.find("{")
    end = text.rfind("}") + 1
    if start == -1:
        raise Exception(f"No JSON found in response: {text[:200]}")
    return json.loads(text[start:end])


def send_reply(msg_id, body):
    # Build reply using himalaya template reply, then send
    full_body = body.strip() + "\n\n" + SIGNATURE
    # Get the reply template
    tmpl_out, _, code = run(f"himalaya template reply --account gmail {msg_id} 2>/dev/null")
    if code != 0:
        log(f"Failed to get reply template for {msg_id}")
        return False

    # Parse headers from template, inject body
    lines = tmpl_out.split("\n")
    headers = []
    for line in lines:
        if line == "" or (headers and not line.startswith(("From:", "To:", "Cc:", "Bcc:", "Subject:", "In-Reply-To:", "References:", "Message-Id:"))):
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

        # Truncate to avoid huge token usage
        email_excerpt = content[:3000]

        try:
            decision = ask_claude(email_excerpt)
            log(f"  🤖 Claude says — reply: {decision['should_reply']} | reason: {decision['reason']}")

            if decision["should_reply"]:
                send_reply(msg_id, decision["reply_body"])
            else:
                log(f"  ⏭️  Skipping reply.")
        except Exception as e:
            log(f"  ❌ Claude error: {e}")

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
