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
LOCK_FILE   = Path(__file__).parent / "astrid_mail.lock"

GEMINI_MODEL = "gemini-flash-lite-latest"
HIMALAYA_BIN = "/usr/local/bin/himalaya"

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
    print(line)  # cron redirects stdout → log file


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
        f"{HIMALAYA_BIN} envelope list --account {account} --output json --page-size 20 2>/dev/null"
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
    out, err, code = run(f"{HIMALAYA_BIN} message read --account {account} {msg_id} 2>/dev/null")
    return out if code == 0 else ""


def send_reply(account, msg_id, body, signature):
    full_body = body.strip() + "\n\n" + signature
    tmpl_out, _, code = run(f"{HIMALAYA_BIN} template reply --account {account} {msg_id} 2>/dev/null")
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
        f"{HIMALAYA_BIN} template send --account {account}",
        shell=True, input=message, capture_output=True, text=True
    )
    if proc.returncode == 0:
        log(f"✅ Reply sent for message {msg_id}")
        return True
    else:
        log(f"❌ Failed to send reply for {msg_id}: {proc.stderr}")
        return False


def mark_seen(account, msg_id):
    run(f"{HIMALAYA_BIN} flag add --account {account} {msg_id} --flag seen 2>/dev/null")

# ── LLM Decision (Gemini Flash Lite) ─────────────────────────────────────────

def ask_gemini_triage(api_key, email_content):
    """
    Stage 1: Gemini Flash Lite decides whether the email needs a reply.
    Fast (~1s), cheap. Returns True/False + reason.
    """
    from google import genai

    triage_prompt = (
        "You are an email triage assistant. Given an email, decide if it needs a human reply.\n"
        "Skip: newsletters, marketing, automated notifications, receipts, alerts, spam.\n"
        "Reply needed: direct questions or messages from real people.\n\n"
        "Respond ONLY with valid JSON: "
        "{\"should_reply\": true/false, \"reason\": \"brief reason\"}\n\n"
        f"Email:\n\n{email_content}"
    )

    client = genai.Client(api_key=api_key)
    log(f"  ⚡ Gemini triage ({GEMINI_MODEL})...")
    response = client.models.generate_content(model=GEMINI_MODEL, contents=triage_prompt)
    text = response.text.strip()

    if "```" in text:
        text = text.split("```")[1].split("\n", 1)[-1].strip()
    start, end = text.find("{"), text.rfind("}") + 1
    if start == -1:
        raise Exception(f"No JSON in triage response: {text[:200]}")
    return json.loads(text[start:end])


def ask_openclaw_reply(sender_name, email_content):
    """
    Stage 2: OpenClaw agent (Claude) generates the actual reply.
    Slower (2-5 min) but uses full Astrid context — memory, personality, projects.
    Only called when Gemini decides a reply is needed.
    """
    prompt = (
        f"You have received an email. Write a reply as yourself ({sender_name}).\n"
        f"Be warm but direct. No corporate speak. Sound like yourself.\n"
        f"Do NOT include a greeting like 'Dear...' or a signature — those are added separately.\n"
        f"Just write the body of the reply.\n\n"
        f"Email received:\n\n{email_content}\n\n"
        f"Reply body:"
    )
    log(f"  🤖 OpenClaw agent generating reply (may take 2-5 min)...")

    # Ensure NVM-managed openclaw is on PATH even when called from cron/subprocess
    env = os.environ.copy()
    nvm_bin = os.path.expanduser("~/.nvm/versions/node/v24.14.0/bin")
    if nvm_bin not in env.get("PATH", ""):
        env["PATH"] = nvm_bin + ":" + env.get("PATH", "")

    proc = subprocess.run(
        ["openclaw", "agent", "--agent", "main", "--message", prompt],
        capture_output=True, text=True, timeout=600, env=env
    )
    text = (proc.stdout or "").strip()

    # Detect known error patterns that openclaw may write to stdout with exit code 0
    error_patterns = [
        "LLM request rejected",
        "Output blocked by content filtering",
        "Gateway agent failed",
        "openclaw agent failed",
        "Error:",
    ]
    for pat in error_patterns:
        if pat in text:
            raise Exception(f"openclaw agent output contained error marker ({pat!r}): {text[:300]}")

    if proc.returncode != 0 or not text:
        raise Exception(f"openclaw agent failed: {proc.stderr[:200]}")
    return text

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
            # Stage 1: fast triage via Gemini (~1s)
            triage = ask_gemini_triage(api_key, content[:3000])
            log(f"  ⚡ Triage — reply: {triage['should_reply']} | reason: {triage['reason']}")

            if triage["should_reply"]:
                # Stage 2: full reply via OpenClaw/Claude (2-5 min, lock prevents cron clashes)
                reply_body = ask_openclaw_reply(name, content[:3000])
                send_reply(account, msg_id, reply_body, signature)
            else:
                log(f"  ⏭️  Skipping reply.")

            # Only mark seen after successful processing
            mark_seen(account, msg_id)
        except Exception as e:
            log(f"  ❌ Error: {e} — email left unread for retry next run")

    save_seen_ids(seen_ids | new_ids)


def run_with_lock(config):
    """
    Acquire a lock file before running. If another instance is already
    running (e.g. OpenClaw reply is taking >5 min), exit immediately.
    This prevents cron overlap and duplicate replies.
    """
    if LOCK_FILE.exists():
        # Check if the PID inside is still alive
        try:
            pid = int(LOCK_FILE.read_text().strip())
            os.kill(pid, 0)  # signal 0 = just check existence
            log(f"⏸️  Another instance is running (PID {pid}), skipping this run.")
            sys.exit(0)
        except (ProcessLookupError, ValueError):
            log("⚠️  Stale lock file found, removing and continuing.")
            LOCK_FILE.unlink(missing_ok=True)

    LOCK_FILE.write_text(str(os.getpid()))
    try:
        process_emails(config)
    finally:
        LOCK_FILE.unlink(missing_ok=True)


if __name__ == "__main__":
    config = load_config()
    poll_interval = int(config.get("POLL_INTERVAL", 180))

    if "--once" in sys.argv:
        run_with_lock(config)
    else:
        log("🚀 Email Daemon started")
        while True:
            try:
                run_with_lock(config)
            except SystemExit:
                pass  # lock was held, just wait and retry
            except Exception as e:
                log(f"💥 Unexpected error: {e}")
            time.sleep(poll_interval)
