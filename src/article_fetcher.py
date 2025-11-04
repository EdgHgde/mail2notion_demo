# src/article_fetcher.py
from __future__ import annotations
# ✅ 표준 라이브러리만 top-level
import re, json
from datetime import datetime, timezone

# ---- 가벼운 유틸만 남김 ----
def _strip_invisibles(s: str) -> str:
    return re.sub(r"[\u200b-\u200f\u2028\u2029\u2060]+", "", s or "")

def _try_parse_iso(s: str) -> datetime | None:
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
            pass
    return None

def _parse_any_date(text: str) -> datetime | None:
    patterns = [
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z",
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:[+-]\d{2}:\d{2})",
        r"\d{4}/\d{2}/\d{2}\s+\d{2}:\d{2}(?::\d{2})?",
    ]
    for pat in patterns:
        m = re.search(pat, text or "")
        if m:
            dt = _try_parse_iso(m.group(0))
            if dt:
                return dt
    return None

def _fmt_kst(dt: datetime) -> str:
    try:
        import zoneinfo  # lazy
        KST = zoneinfo.ZoneInfo("Asia/Seoul")
    except Exception:
        KST = timezone.utc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(KST).strftime("%Y.%m.%d. %H:%M")

def _extract_article_datetime(html: str) -> str | None:
    # ✅ 무거운 bs4는 여기서 임포트
    try:
        from bs4 import BeautifulSoup
    except Exception:
        return None

    soup = BeautifulSoup(html or "", "html.parser")

    # 1) 메타 태그
    meta_specs = [
        ("meta", {"property": "article:published_time"}),
        ("meta", {"property": "article:modified_time"}),
        ("meta", {"property": "og:updated_time"}),
        ("meta", {"name": "date"}),
        ("time", {"datetime": True}),
    ]
    for tag, attrs in meta_specs:
        el = soup.find(tag, attrs)
        if el:
            val = el.get("content") or el.get("datetime") or ""
            dt = _parse_any_date(val)
            if dt:
                return _fmt_kst(dt)

    # 2) JSON-LD
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "{}")
        except Exception:
            continue
        objs = data if isinstance(data, list) else [data]
        for obj in objs:
            if not isinstance(obj, dict):
                continue
            if obj.get("@type") in ("NewsArticle", "Article", "BlogPosting"):
                for key in ("datePublished", "dateModified", "dateCreated"):
                    if key in obj:
                        dt = _parse_any_date(str(obj[key]))
                        if dt:
                            return _fmt_kst(dt)

    # 3) 본문 전체에서 마지막 시도
    dt = _parse_any_date(html or "")
    return _fmt_kst(dt) if dt else None

def fetch_article_markdown(url: str, timeout: int = 15) -> tuple[str, str, str] | None:
    """
    (title, content_markdown, pub_time_kst) 반환.
    실패/페이월/엑세스 거부 시 None.
    """
    # ✅ 무거운 것들 전부 함수 안에서 임포트
    try:
        import requests
        from readability import Document
        from markdownify import markdownify as md
        from bs4 import BeautifulSoup
    except Exception:
        # 패키지 하나라도 없으면 깔끔히 포기
        return None

    UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
          "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36")

    try:
        resp = requests.get(url, headers={"User-Agent": UA}, timeout=timeout, allow_redirects=True)
        if resp.status_code >= 400:
            return None

        html = _strip_invisibles(resp.text or "")
        if len(html) < 800:
            return None

        # 날짜 먼저
        pub_kst = _extract_article_datetime(html) or ""

        # 1차: readability
        doc = Document(html)
        title = (doc.short_title() or "").strip()
        content_html = doc.summary(html_partial=True)
        content_md = _strip_invisibles(md(content_html).strip())

        # 2차: 빈약하면 CSS 선택자로 보강
        if len(content_md) < 180:
            soup = BeautifulSoup(html, "html.parser")
            for sel in [
                "article", "[itemprop='articleBody']", ".article-body", ".content__article-body",
                ".story-content", ".sa-art", ".post-content", "#article-body", ".body__inner-container",
            ]:
                el = soup.select_one(sel)
                if el:
                    more = _strip_invisibles(md(str(el)).strip())
                    if len(more) > len(content_md):
                        content_md = more

        if not content_md or len(content_md) < 180:
            return None

        if not title:
            m = re.search(r"<title>(.*?)</title>", html, re.I | re.S)
            if m:
                title = _strip_invisibles(m.group(1)).strip()

        return title, content_md, pub_kst

    except requests.RequestException:
        return None
