"""Alert channels for the guard. Each channel is used only when its settings are in .env:

    TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID       Telegram bot message
    SMTP_HOST + ALERT_EMAIL_TO                   e-mail (SMTP_PORT 587 with STARTTLS, SMTP_USER, SMTP_PASSWORD,
                                                 ALERT_EMAIL_FROM optional)
    NTFY_URL                                     ntfy.sh topic URL (push to the phone app)

With none of them the dashboard's status line is the only signal.
"""
from __future__ import annotations

import json
import logging
import os
import smtplib
import urllib.parse
import urllib.request
from email.message import EmailMessage

log = logging.getLogger("notify")


def _telegram(title: str, text: str) -> None:
    token, chat = os.environ["TELEGRAM_BOT_TOKEN"], os.environ["TELEGRAM_CHAT_ID"]
    body = urllib.parse.urlencode({"chat_id": chat, "text": f"{title}\n\n{text}"}).encode()
    urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data=body, timeout=20).read()


def _email(title: str, text: str) -> None:
    msg = EmailMessage()
    msg["Subject"] = title
    msg["From"] = os.environ.get("ALERT_EMAIL_FROM") or os.environ.get("SMTP_USER") or os.environ["ALERT_EMAIL_TO"]
    msg["To"] = os.environ["ALERT_EMAIL_TO"]
    msg.set_content(text)
    port = int(os.environ.get("SMTP_PORT") or 587)
    if port == 465:
        smtp = smtplib.SMTP_SSL(os.environ["SMTP_HOST"], port, timeout=30)
    else:
        smtp = smtplib.SMTP(os.environ["SMTP_HOST"], port, timeout=30)
        smtp.starttls()
    with smtp:
        if os.environ.get("SMTP_USER"):
            smtp.login(os.environ["SMTP_USER"], os.environ.get("SMTP_PASSWORD", ""))
        smtp.send_message(msg)


def _ntfy(title: str, text: str) -> None:
    url = os.environ["NTFY_URL"]
    # the title goes in the query string: HTTP headers cannot carry Cyrillic
    url += ("&" if "?" in url else "?") + urllib.parse.urlencode({"title": title})
    req = urllib.request.Request(url, data=text.encode(), method="POST")
    urllib.request.urlopen(req, timeout=20).read()


CHANNELS = {
    "telegram": (("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"), _telegram),
    "email": (("SMTP_HOST", "ALERT_EMAIL_TO"), _email),
    "ntfy": (("NTFY_URL",), _ntfy),
}


def configured() -> list[str]:
    return [name for name, (keys, _) in CHANNELS.items() if all(os.environ.get(k) for k in keys)]


def send(title: str, text: str) -> dict[str, str]:
    """Send to every configured channel; returns {channel: "ok" | error}. Never raises."""
    out = {}
    for name in configured():
        try:
            CHANNELS[name][1](title, text)
            out[name] = "ok"
        except Exception as exc:  # one broken channel must not stop the others
            log.warning("alert via %s failed: %s", name, exc)
            out[name] = str(exc)[:200]
    if out:
        log.info("alert sent: %s", json.dumps(out))
    return out
