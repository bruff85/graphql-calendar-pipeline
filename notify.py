#!/usr/bin/env python3
"""
Gmail notification module for lunch calendar scripts.
Sends success/failure emails via Gmail API using OAuth credentials.
"""

import os
import base64
import json
from email.mime.text import MIMEText
from datetime import datetime

import requests


def get_access_token():
    """Get a fresh access token using the refresh token."""
    client_id = os.environ.get("GMAIL_CLIENT_ID")
    client_secret = os.environ.get("GMAIL_CLIENT_SECRET")
    refresh_token = os.environ.get("GMAIL_REFRESH_TOKEN")

    if not all([client_id, client_secret, refresh_token]):
        raise ValueError("Missing Gmail credentials in environment variables.")

    response = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=30
    )
    response.raise_for_status()
    return response.json()["access_token"]


def send_email(subject, body):
    """Send an email via Gmail API."""
    to_email = os.environ.get("NOTIFY_EMAIL")
    if not to_email:
        print("  No NOTIFY_EMAIL set — skipping email notification.")
        return

    try:
        access_token = get_access_token()

        message = MIMEText(body, "plain")
        message["to"] = to_email
        message["from"] = to_email
        message["subject"] = subject

        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()

        response = requests.post(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            json={"raw": raw},
            timeout=30
        )
        response.raise_for_status()
        print(f"  Email notification sent to {to_email} ✅")

    except Exception as e:
        print(f"  Failed to send email notification: {e}")


def notify_success(calendar_name, month_label, event_count):
    """Send a success notification email."""
    subject = f"✅ {calendar_name} — {month_label} Menu Loaded"
    body = f"""Hello,

The {calendar_name} has been successfully updated!

Month: {month_label}
Events generated: {event_count} school days

Your calendar subscription will refresh automatically within a few hours.

Subscribe link:
- LCUSD: https://bruff85.github.io/lcusd-lunch-calendar/lunch.ics
- Arroyo: https://bruff85.github.io/arroyo-lunch-calendar/lunch.ics

— Lunch Calendar Bot
{datetime.now().strftime("%B %d, %Y at %I:%M %p")}
"""
    send_email(subject, body)


def notify_failure(calendar_name, target_month_label, reason):
    """Send a failure notification email."""
    subject = f"⚠️ {calendar_name} — {target_month_label} Menu Not Found"
    body = f"""Hello,

The {calendar_name} was unable to load the {target_month_label} menu.

Reason: {reason}

The script will automatically retry at the next scheduled run.
If this continues, you may need to manually check the school website
and trigger the workflow manually from GitHub Actions.

GitHub Actions:
- LCUSD: https://github.com/bruff85/lcusd-lunch-calendar/actions
- Arroyo: https://github.com/bruff85/arroyo-lunch-calendar/actions

— Lunch Calendar Bot
{datetime.now().strftime("%B %d, %Y at %I:%M %p")}
"""
    send_email(subject, body)
