"""Alerts: one message to wherever the team listens. Used by meridian.validate (data errors and warnings) and the
Airflow DAG (failed tasks).

Where it goes is settings.toml [alerts]: email addresses, SMTP server, and which events alert at all. Secrets (the
Slack webhook URL, an SMTP password) live in environment variables that [alerts] names, never in git. Nothing
configured: the alert is only logged. A channel that fails is logged too: an alert must never break the run it reports
on.
"""
import json
import logging
import os
import smtplib
import urllib.request
from email.message import EmailMessage

from meridian.common import load_settings, log


def send(subject, lines=(), event="error", settings=None):
    """Log the alert, then send it to every configured channel if [alerts] on lists the event
    ('failure', 'error', 'warn'). Returns the channels that took it."""
    a = load_settings("alerts", settings)
    lines = list(lines)
    if len(lines) > a.get("max_lines", 20):
        lines = lines[:a.get("max_lines", 20)] + [f"... and {len(lines) - a.get('max_lines', 20)} more"]
    text = "\n".join([subject, *lines])
    log(f"ALERT {text}", level=logging.WARNING if event == "warn" else logging.ERROR)
    if event not in a.get("on", ["failure", "error", "warn"]):
        return []
    sent, timeout = [], a.get("timeout_s", 10)
    if hook := os.environ.get(a.get("slack_webhook_env", "MERIDIAN_SLACK_WEBHOOK")):
        try:
            req = urllib.request.Request(hook, json.dumps({"text": text}).encode(),
                                         {"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=timeout).close()
            sent.append("slack")
        except (OSError, ValueError) as e:  # ValueError: a malformed webhook URL
            log(f"alert: Slack failed: {e}", level=logging.ERROR)
    if to := a.get("email_to"):
        try:
            msg = EmailMessage()
            msg["Subject"], msg["From"] = f"[meridian] {subject}", a.get("email_from", "meridian@localhost")
            msg["To"] = ", ".join(to)
            msg.set_content(text)
            with smtplib.SMTP(a.get("smtp_host", "localhost"), a.get("smtp_port", 25), timeout=timeout) as s:
                if a.get("smtp_starttls"):
                    s.starttls()
                if user := os.environ.get(a.get("smtp_user_env", "")):
                    s.login(user, os.environ.get(a.get("smtp_password_env", ""), ""))
                s.send_message(msg)
            sent.append("email")
        except (OSError, ValueError, smtplib.SMTPException) as e:
            log(f"alert: email failed: {e}", level=logging.ERROR)
    return sent
