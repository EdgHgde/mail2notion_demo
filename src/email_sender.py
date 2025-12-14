# src/email_sender.py
from __future__ import annotations
import base64
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart


def send_email(service, to, subject, body_md, bcc=None):
    message = MIMEMultipart()
    message["To"] = ", ".join(to)
    message["Subject"] = subject

    if bcc:
        message["Bcc"] = ", ".join(bcc)

    message.attach(MIMEText(body_md, "plain", "utf-8"))

    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()

    service.users().messages().send(
        userId="me",
        body={"raw": raw}
    ).execute()
