# 📬 astrid-email-daemon

An AI-powered email auto-responder for Gmail, built around [Himalaya](https://github.com/pimalaya/himalaya) (CLI email client) and [OpenClaw](https://openclaw.ai) (AI agent runtime).

When an unread email arrives, the daemon reads it, asks an LLM whether a reply is needed and what to say, then sends the reply automatically — complete with signature.

---

## How It Works

```
Cron (every 5 min)
    └─▶ astrid_mail.py --once
            ├─ himalaya: fetch unread emails
            ├─ For each new email:
            │     ├─ himalaya: read full message
            │     ├─ openclaw agent: decide reply (JSON)
            │     ├─ himalaya: send reply (if needed)
            │     └─ himalaya: mark as seen
            └─ Save seen IDs to seen_ids.json
```

---

## Requirements

| Tool | Purpose | Install |
|------|---------|---------|
| Python 3 | Runtime | Built-in on macOS |
| [himalaya](https://github.com/pimalaya/himalaya) | Email CLI (IMAP + SMTP) | `curl -sSL https://raw.githubusercontent.com/pimalaya/himalaya/master/install.sh \| bash` |
| google-genai | Google Gemini SDK | `pip3 install google-genai` |

---

## API Key

This project uses the **Google Gemini API** for LLM inference.

| Detail | Value |
|--------|-------|
| Provider | Google Gemini |
| Model | `gemini-flash-lite-latest` (lightest available — fast + cheap) |
| Purpose | Email triage: decide if an email needs a reply + generate the reply |
| Key location | `~/.config/astrid/api_keys.env` → `GEMINI_API_KEY=...` |
| Committed to git? | ❌ No — file is in `.gitignore` |

To set up your own key:
1. Go to [aistudio.google.com/apikey](https://aistudio.google.com/apikey) and generate a key
2. Add it to `~/.config/astrid/api_keys.env`:
   ```
   GEMINI_API_KEY=your_key_here
   ```

---

## Setup

### 1. Configure Himalaya

Set up your Gmail account in `~/.config/himalaya/config.toml`:

```toml
[accounts.gmail]
default = true
email = "you@gmail.com"
display-name = "Your Name"
signature = "— Your Name\nYour tagline\nyou@gmail.com"
signature-delim = "-- \n"

[accounts.gmail.folder.aliases]
sent = "[Gmail]/Sent Mail"
drafts = "[Gmail]/Drafts"
trash = "[Gmail]/Bin"

[accounts.gmail.backend]
type = "imap"
host = "imap.gmail.com"
port = 993
encryption.type = "tls"
login = "you@gmail.com"
auth.type = "password"
auth.raw = "YOUR_APP_PASSWORD"   # Gmail App Password (not your main password)

[accounts.gmail.message.send]
backend.type = "smtp"
backend.host = "smtp.gmail.com"
backend.port = 587
backend.encryption.type = "start-tls"
backend.login = "you@gmail.com"
backend.auth.type = "password"
backend.auth.raw = "YOUR_APP_PASSWORD"
```

> **Gmail App Password:** Go to [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords) and generate one. Required if 2FA is enabled.

### 2. Configure OpenClaw

Make sure OpenClaw is set up with a working model (e.g. `anthropic/claude-haiku-4-5`):

```bash
openclaw status
openclaw models list
```

### 3. Customise the Daemon

Edit `astrid_mail.py` to set your identity and signature:

```python
ACCOUNT = "gmail"   # Must match your himalaya account name

SIGNATURE = """-- 
— Your Name 💫
Your tagline
you@gmail.com"""

SYSTEM_PROMPT = """You are [Your Name] — [describe yourself].
...
"""
```

### 4. Test It

```bash
python3 astrid_mail.py --once
```

Check the log:

```bash
tail -f astrid_mail.log
```

### 5. Set Up Cron (runs every 5 minutes)

```bash
crontab -e
```

Add:

```
*/5 * * * * /usr/bin/python3 /path/to/astrid_mail.py --once >> /path/to/astrid_mail.log 2>&1
```

---

## Files

```
astrid-email-daemon/
├── astrid_mail.py     # Main daemon script
├── seen_ids.json      # Auto-generated: tracks processed email IDs
├── astrid_mail.log    # Auto-generated: full run log
└── README.md
```

---

## LLM Decision Format

The daemon instructs the LLM to return a structured JSON decision:

```json
{
  "should_reply": true,
  "reason": "Direct question from a person, needs a response",
  "reply_body": "Hey! Thanks for reaching out..."
}
```

Emails the LLM will typically **skip**:
- Newsletters and marketing
- Automated notifications (GitHub, Google alerts, receipts)
- Spam

Emails it will **reply to**:
- Direct questions or messages from real people
- Anything requiring acknowledgement or action

---

## Running as a Persistent Daemon (optional)

Instead of cron, you can run it continuously (polls every 3 minutes internally):

```bash
python3 astrid_mail.py
```

Or as a background process:

```bash
nohup python3 astrid_mail.py > astrid_mail.log 2>&1 &
```

---

## Notes

- The LLM call via `openclaw agent` can take 2–5 minutes depending on system load and model response time. This is normal — email is async anyway.
- For faster LLM responses, set `ANTHROPIC_API_KEY` in your environment and swap the `ask_claude()` function to use the Anthropic Python SDK directly.
- Emails are only processed once (tracked in `seen_ids.json`). Deleting this file will reprocess all current unread emails.

---

## License

MIT
