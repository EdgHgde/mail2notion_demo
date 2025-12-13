# src/email_sender.py
from __future__ import annotations
import base64
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart


def send_email(
    service,
    to: list[str],
    subject: str,
    body_md: str,
    sender: str = "me",
):
    """
    Gmail API를 이용해 이메일 발송
    service: gmail API service
    to: 수신자 리스트
    subject: 메일 제목
    body_md: 마크다운 요약 본문
    """

    msg = MIMEMultipart()
    msg["To"] = ", ".join(to)
    msg["From"] = sender
    msg["Subject"] = subject

    # Markdown 그대로 보내도 되고,
    # 나중에 HTML 변환해도 됨
    msg.attach(MIMEText(body_md, "plain", "utf-8"))

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()

    service.users().messages().send(
        userId="me",
        body={"raw": raw},
    ).execute()
    