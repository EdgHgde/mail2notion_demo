# src/gmail_client.py
from __future__ import annotations

import base64
import os
import re
from typing import Dict, List, Tuple, Optional

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from .datetime_utils import parse_rfc2822_date

from bs4 import BeautifulSoup
from markdownify import markdownify as md
from rich import print
import datetime

# =========================
# 설정
# =========================
SCOPES = ["https://www.googleapis.com/auth/gmail.send",
          "https://www.googleapis.com/auth/gmail.readonly"]
TOKEN_FILE = "token.json"

# 메일 본문에서 발견되는 뉴스 링크 우선 도메인 (필요시 추가)
NEWS_DOMAINS = (
    "seekingalpha.com",
    "finance.yahoo.com",
    "cnbc.com",
    "bloomberg.com",
    "reuters.com",
)

# =========================
# 유틸
# =========================
def _safe_b64decode(data: str) -> str:
    """URL-safe base64 문자열을 안전하게 디코딩."""
    if not data:
        return ""
    # URL-safe padding 보정
    padding = "=" * ((4 - len(data) % 4) % 4)
    try:
        return base64.urlsafe_b64decode(data + padding).decode(errors="ignore")
    except Exception:
        return ""

def _headers_dict(payload: Dict) -> Dict[str, str]:
    """Gmail payload 헤더를 소문자 키 딕셔너리로."""
    headers = {}
    for h in payload.get("headers", []) or []:
        name = (h.get("name") or "").lower()
        val = h.get("value") or ""
        headers[name] = val
    return headers

# =========================
# 인증/서비스
# =========================
def load_creds(credentials_file: str) -> Credentials:
    """OAuth 토큰을 로드/생성."""
    creds: Optional[Credentials] = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            print("[cyan]Refreshing Gmail token…[/cyan]")
            creds.refresh(Request())
        else:
            print("[cyan]Launching browser for Gmail OAuth…[/cyan]")
            flow = InstalledAppFlow.from_client_secrets_file(credentials_file, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
    return creds

def get_service(creds: Credentials):
    """Gmail API 서비스 생성."""
    return build("gmail", "v1", credentials=creds)

# =========================
# 메시지 검색/조회
# =========================
def search_messages(service, query: str, max_results: int = 20) -> List[str]:
    """
    Gmail 검색 쿼리로 메시지 ID 목록을 최신순으로 반환.
        예: 'from:account@seekingalpha.com newer_than:1d'
    """
    res = service.users().messages().list(
        userId="me", q=query, maxResults=max_results
    ).execute()
    return [m["id"] for m in res.get("messages", [])]

def get_message(service, msg_id: str) -> Dict:
    """메시지 전체(payload 포함) 조회."""
    return service.users().messages().get(
        userId="me", id=msg_id, format="full"
    ).execute()

# =========================
# 첨부/본문 파싱
# =========================
def get_attachment(service, msg_id: str, attachment_id: str) -> str:
    """첨부 파트 데이터(base64)를 디코딩해 텍스트로 반환."""
    try:
        att = service.users().messages().attachments().get(
            userId="me", messageId=msg_id, id=attachment_id
        ).execute()
        return _safe_b64decode(att.get("data", ""))
    except HttpError as e:
        print(f"[red]Attachment fetch error:[/red] {e}")
        return ""

def _parts_to_text(service, msg_id: str, payload: Dict) -> Tuple[str, str]:
    """
    MIME 파트를 순회해 (plain_text, html_text) 조합.
    - 본문 body.data
    - 첨부 body.attachmentId (text/* 만 안전히 로딩)
    """
    plain, html = "", ""

    def walk(part: Dict):
        nonlocal plain, html
        mime = part.get("mimeType", "") or ""
        body = part.get("body", {}) or {}

        data = body.get("data")
        att_id = body.get("attachmentId")

        content = ""
        if data:
            content = _safe_b64decode(data)
        elif att_id and mime.startswith("text/"):
            # 첨부에 들어있는 텍스트/HTML도 로딩
            content = get_attachment(service, msg_id, att_id)

        if content:
            if mime == "text/plain":
                plain += content + "\n"
            elif mime == "text/html":
                html += content + "\n"

        for p in part.get("parts", []) or []:
            walk(p)

    walk(payload)
    return plain.strip(), html.strip()

def extract_text_from_message(service, msg: Dict) -> str:
    """
    메시지에서 '제목/보낸이' 메타 + 본문 텍스트를 조합.
    - HTML만 있을 경우 markdownify로 텍스트화
    - 둘 다 없으면 snippet을 대용
    """
    payload = msg.get("payload", {}) or {}
    headers = _headers_dict(payload)

    subj = headers.get("subject", "(no subject)")
    frm = headers.get("from", "(unknown sender)")

    plain, html = _parts_to_text(service, msg.get("id", ""), payload)
    if html and not plain:
        plain = md(html)

    if not plain:
        # snippet은 아주 짧으므로 후속 로직에서 길이 검증 권장
        plain = msg.get("snippet", "")

    meta = f"Subject: {subj}\nFrom: {frm}\n\n"
    return meta + (plain or "(empty)")

# =========================
# URL 추출 (기사 링크용)
# =========================
_URL_RE = re.compile(r"https?://[^\s)>\]]+", re.I)

def extract_urls_from_message(msg: Dict) -> List[str]:
    """
    메시지의 HTML <a href>와 text/plain 내 URL을 모아
    뉴스 도메인 우선순위로 정렬해 반환.
    """
    payload = msg.get("payload", {}) or {}
    urls: List[str] = []

    def collect_html_links(part: Dict):
        mime = part.get("mimeType", "") or ""
        body = part.get("body", {}) or {}
        data = body.get("data")
        if data and mime == "text/html":
            html = _safe_b64decode(data)
            soup = BeautifulSoup(html, "html.parser")
            for a in soup.find_all("a", href=True):
                urls.append(a["href"])
        for p in part.get("parts", []) or []:
            collect_html_links(p)

    def collect_text_links(part: Dict):
        mime = part.get("mimeType", "") or ""
        body = part.get("body", {}) or {}
        data = body.get("data")
        if data and mime == "text/plain":
            text = _safe_b64decode(data)
            urls.extend(_URL_RE.findall(text))
        for p in part.get("parts", []) or []:
            collect_text_links(p)

    collect_html_links(payload)
    collect_text_links(payload)

    # 중복 제거
    uniq: List[str] = []
    seen = set()
    for u in urls:
        if u not in seen:
            seen.add(u)
            uniq.append(u)

    # 뉴스 도메인 우선 정렬
    def score(u: str) -> int:
        for i, dom in enumerate(NEWS_DOMAINS):
            if dom in u:
                return -(10 - i)  # 앞쪽 도메인일수록 높은 우선순위
        return 0

    uniq.sort(key=score)
    return uniq

def extract_email_dates(msg: Dict) -> Tuple[Optional[datetime], Optional[int]]:
    """
    이메일 헤더 Date → datetime(UTC), Gmail internalDate(ms) → int
    """
    payload = msg.get("payload", {}) or {}
    headers = { (h.get("name") or "").lower(): (h.get("value") or "") for h in payload.get("headers", []) }
    hdr_date = headers.get("date")
    dt_hdr = parse_rfc2822_date(hdr_date) if hdr_date else None
    internal_ms = None
    try:
        internal_ms = int(msg.get("internalDate")) if msg.get("internalDate") else None
    except Exception:
        internal_ms = None
    return dt_hdr, internal_ms
