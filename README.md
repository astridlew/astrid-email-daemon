# 📬 astrid-email-daemon

An AI-powered email auto-responder for Gmail, built with [Himalaya](https://github.com/pimalaya/himalaya) (CLI email client) and Google Gemini (LLM triage).

When an unread email arrives, the daemon reads it, asks Gemini whether a reply is needed and what to say, then sends the reply automatically — complete with your custom signature.

---

## How It Works

```
Cron (every 5 min)
    └─▶ astrid_mail.py --once
            ├─ himalaya: fetch unread emails
            ├─ For each new email:
            │     ├─ himalaya: read full message
            │     ├─ Gemini Flash Lite: decide reply (JSON)
            │     ├─ himalaya: send reply (if needed)
            │     └─ himalaya: mark as seen
            └─ Save seen IDs to seen_ids.json
```

---

## Requirements

| Tool | Purpose | Install |
|------|---------|---------|
| Python 3 | Runtime | Built-in on macOS / `sudo apt install python3` |
| [himalaya](https://github.com/pimalaya/himalaya) | Email CLI (IMAP + SMTP) | See [himalaya install docs](https://github.com/pimalaya/himalaya) |
| google-genai | Google Gemini SDK | `pip3 install google-genai` |

---

## API Key

| Detail | Value |
|--------|-------|
| Provider | Google Gemini |
| Model | `gemini-flash-lite-latest` (lightest available — fast, cheap, sufficient for triage) |
| Purpose | Decide if an email needs a reply; generate the reply text |
| Key location | `config.env` → `GEMINI_API_KEY=...` |
| Committed to git? | ❌ No — `config.env` is in `.gitignore` |

Get a free API key at: [aistudio.google.com/apikey](https://aistudio.google.com/apikey)

---

## Setup

### 1. Configure Himalaya

Create `~/.config/himalaya/config.toml` for your Gmail account:

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
auth.raw = "YOUR_APP_PASSWORD"

[accounts.gmail.message.send]
backend.type = "smtp"
backend.host = "smtp.gmail.com"
backend.port = 587
backend.encryption.type = "start-tls"
backend.login = "you@gmail.com"
backend.auth.type = "password"
backend.auth.raw = "YOUR_APP_PASSWORD"
```

> **Gmail App Password:** Required if 2FA is enabled. Generate one at [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords).

### 2. Create Your Config

```bash
cp config.example.env config.env
```

Edit `config.env` with your details:

```env
GEMINI_API_KEY=your_gemini_api_key_here
HIMALAYA_ACCOUNT=gmail
SENDER_NAME=Your Name
SENDER_EMAIL=you@example.com
SENDER_TAGLINE=Your tagline here
POLL_INTERVAL=180
```

### 3. Install Dependencies

```bash
pip3 install google-genai
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
├── astrid_mail.py        # Main daemon script
├── config.example.env    # Template — copy to config.env and fill in
├── config.env            # Your config + API key (gitignored, never committed)
├── seen_ids.json         # Auto-generated: tracks processed email IDs
├── astrid_mail.log       # Auto-generated: full run log
└── README.md
```

---

## LLM Decision Format

The daemon instructs Gemini to return a structured JSON decision:

```json
{
  "should_reply": true,
  "reason": "Direct question from a real person",
  "reply_body": "Hey! Thanks for reaching out..."
}
```

**Emails skipped:** newsletters, marketing, automated alerts, receipts, spam  
**Emails replied to:** direct questions, messages from real people needing a response

---

## Running as a Persistent Daemon (optional)

Instead of cron, run it continuously:

```bash
python3 astrid_mail.py
```

Or in the background:

```bash
nohup python3 astrid_mail.py > astrid_mail.log 2>&1 &
```

---

## License

MIT
