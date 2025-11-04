# src/datetime_utils.py
from __future__ import annotations
import re, json
from datetime import datetime, timezone
from typing import Optional, Tuple, Any

KST = timezone.utc  # 임시; 아래에서 교체
try:
    import zoneinfo  # py3.9+
    KST = zoneinfo.ZoneInfo("Asia/Seoul")
except Exception:
    pass

_ISO_CANDIDATES = [
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z",
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:[+-]\d{2}:\d{2})",
    r"\d{4}/\d{2}/\d{2}\s+\d{2}:\d{2}(?::\d{2})?",
]

def _try_parse_iso(s: str) -> Optional[datetime]:
    s = s.strip()
    fmts = [
        "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S%z",   "%Y-%m-%dT%H:%M%z",
        "%Y/%m/%d %H:%M:%S",     "%Y/%m/%d %H:%M",
    ]
    for f in fmts:
        try:
            dt = datetime.strptime(s, f)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            continue
    return None

def parse_any_date(text: str) -> Optional[datetime]:
    """본문/메타에서 날짜 비슷한 토큰을 뽑아 UTC 인식 datetime으로."""
    for pat in _ISO_CANDIDATES:
        m = re.search(pat, text)
        if m:
            dt = _try_parse_iso(m.group(0))
            if dt: return dt
    return None

def to_kst(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(KST)

def fmt_kst(dt: datetime) -> str:
    return to_kst(dt).strftime("%Y.%m.%d. %H:%M")

def choose_best_date(article_dt: Optional[datetime],
                     email_hdr_dt: Optional[datetime],
                     gmail_internal_ms: Optional[int]) -> Tuple[str, str]:
    """
    우선순위:
      1) 기사 페이지 메타/JSON-LD 날짜
      2) 이메일 헤더 Date
      3) Gmail internalDate(ms)
    반환: (표시문자열, 출처태그)
    """
    if article_dt:
        return fmt_kst(article_dt), "article"
    if email_hdr_dt:
        return fmt_kst(email_hdr_dt), "email"
    if gmail_internal_ms:
        dt = datetime.fromtimestamp(gmail_internal_ms/1000, tz=timezone.utc)
        return fmt_kst(dt), "gmail"
    return "", "unknown"

def parse_rfc2822_date(date_hdr: str) -> Optional[datetime]:
    # 예: Tue, 04 Nov 2025 05:22:31 -0800
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(date_hdr)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None
