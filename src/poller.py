# src/poller.py
from __future__ import annotations
import os, re, json, time, random, socket
from rich import print

from .config import GOOGLE_CREDENTIALS_FILE, GMAIL_SEARCH_QUERY, GMAIL_PROCESSED_LABEL
from .gmail_client import (
    load_creds, get_service, search_messages, get_message,
    extract_text_from_message, add_label_processed,
    extract_urls_from_message, extract_email_dates
)
from .datetime_utils import choose_best_date
from .formatter import render_markdown, make_filename
from .io_utils import write_markdown
from .article_fetcher import fetch_article_markdown

# -------- Poller settings --------
STATE_FILE = ".state.json"
POLL_INTERVAL_SEC = int(os.getenv("POLL_INTERVAL_SEC", "30"))      # 기본 폴링 주기
POLL_BATCH = int(os.getenv("POLL_BATCH", "10"))                    # 한 번에 처리할 최대 메일 수
IDLE_BACKOFF_MAX = int(os.getenv("IDLE_BACKOFF_MAX", "300"))       # 최대 백오프 (초)
SOCKET_TIMEOUT = int(os.getenv("SOCKET_TIMEOUT", "30"))            # 네트워크 타임아웃
MIN_BODY_LEN = int(os.getenv("MIN_BODY_LEN", "120"))               # 본문 보강 임계치
# ----------------------------------

socket.setdefaulttimeout(SOCKET_TIMEOUT)

def _strip_invisibles(s: str) -> str:
    if s is None:
        return ""
    s = re.sub(r"[\u200b-\u200f\u2028\u2029\u2060]+", "", s)
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    return s.strip()

def _hdr(msg: dict) -> tuple[str, str]:
    headers = {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}
    return headers.get("subject", "(no subject)"), headers.get("from", "(unknown sender)")

def _load_state() -> dict:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"processed_ids": []}

def _save_state(st: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=2)

def _process_one(svc, msg_id: str) -> bool:
    """
    단일 메시지 처리. 성공 시 True, 스킵/실패 시 False.
    """
    try:
        msg = get_message(svc, msg_id)
        subject, sender = _hdr(msg)
        print(f"MSG {msg_id[:8]}: start")
        print(f"MSG {msg_id[:8]}: fetched")
        print(f"MSG {msg_id[:8]}: subject {subject} — {sender}")

        raw = _strip_invisibles(extract_text_from_message(svc, msg))
        print(f"MSG {msg_id[:8]}: raw extracted ({len(raw)} chars)")

        # 날짜 후보
        try:
            dt_email_hdr, internal_ms = extract_email_dates(msg)
            print(f"MSG {msg_id[:8]}: date candidates email={bool(dt_email_hdr)} gmail={bool(internal_ms)}")
        except Exception as e:
            dt_email_hdr, internal_ms = (None, None)
            print(f"[yellow]MSG {msg_id[:8]}: email dates parse fail -> {e}[/yellow]")

        best_date_display, best_src = "", "unknown"

        # 본문 보강 (짧으면 링크 기사 합성 시도)
        composed_text = raw
        body_only = raw.split("\n\n", 1)[-1] if "\n\n" in raw else raw
        if len(body_only.strip()) < MIN_BODY_LEN:
            print(f"MSG {msg_id[:8]}: body short -> try article fetch")
            urls = extract_urls_from_message(msg)
            print(f"MSG {msg_id[:8]}: {len(urls)} url(s) found")
            for u in urls[:3]:
                print(f"MSG {msg_id[:8]}: fetch article {u}")
                try:
                    art = fetch_article_markdown(u)
                except Exception as e:
                    print(f"[yellow]MSG {msg_id[:8]}: article fetch error -> {e}[/yellow]")
                    art = None
                if art:
                    title2, md_article, pub_kst = art
                    composed_text += f"\n\n[링크 기사] {u}\n\n{md_article}"
                    print(f"MSG {msg_id[:8]}: article ok -> {title2}")
                    if pub_kst:
                        best_date_display, best_src = pub_kst, "article"
                    break

        # 날짜 확정 + 헤더 주입
        if not best_date_display:
            disp, src = choose_best_date(None, dt_email_hdr, internal_ms)
            best_date_display, best_src = disp, src
        print(f"MSG {msg_id[:8]}: date -> {best_date_display or '미확인'} ({best_src})")
        composed_text = f"[DETECTED_DATE_KST:{best_date_display or '미확인'}|SOURCE:{best_src}]\n{composed_text}"

        # LLM
        print(f"MSG {msg_id[:8]}: LLM start")
        md = render_markdown(composed_text)
        print(f"MSG {msg_id[:8]}: LLM done")

        outpath = write_markdown(make_filename(msg_id), md)
        print(f"MSG {msg_id[:8]}: saved -> {outpath}")

        if GMAIL_PROCESSED_LABEL:
            try:
                add_label_processed(svc, msg_id, GMAIL_PROCESSED_LABEL)
                print(f"MSG {msg_id[:8]}: labeled {GMAIL_PROCESSED_LABEL}")
            except Exception as e:
                print(f"[yellow]MSG {msg_id[:8]}: label failed -> {e}[/yellow]")

        print(f"MSG {msg_id[:8]}: end")
        return True

    except Exception as e:
        print(f"[red]MSG {msg_id[:8]}: failed -> {e}[/red]")
        return False

def main():
    print("POLL: start")
    print(f"query: '{GMAIL_SEARCH_QUERY}' | interval={POLL_INTERVAL_SEC}s batch={POLL_BATCH}")

    # Gmail 준비
    print("Launching browser for Gmail OAuth…")
    creds = load_creds(GOOGLE_CREDENTIALS_FILE)
    print("GMAIL: creds loaded")
    svc = get_service(creds)
    print("GMAIL: service ready")

    state = _load_state()
    processed = set(state.get("processed_ids", []))
    idle_backoff = POLL_INTERVAL_SEC

    try:
        while True:
            print("\nTICK: search…")
            ids = search_messages(svc, GMAIL_SEARCH_QUERY, max_results=POLL_BATCH)
            print(f"FOUND: {len(ids)} message(s)")

            new_ids = [i for i in ids if i not in processed]
            if not new_ids:
                # idle → 백오프 증가(+지터)
                sleep_s = min(IDLE_BACKOFF_MAX, int(idle_backoff * 1.5))
                jitter = random.randint(0, min(5, sleep_s))
                idle_backoff = sleep_s
                print(f"IDLE: no new messages → sleep {sleep_s + jitter}s")
                time.sleep(sleep_s + jitter)
                continue

            # 새 메일 처리
            idle_backoff = POLL_INTERVAL_SEC  # 성공/작업 발생 시 백오프 초기화
            for msg_id in new_ids:
                ok = _process_one(svc, msg_id)
                if ok:
                    processed.add(msg_id)
                    state["processed_ids"] = list(processed)
                    _save_state(state)

            # 다음 사이클: 기본 인터벌(+지터) 대기
            jitter = random.randint(0, 3)
            print(f"SLEEP: {POLL_INTERVAL_SEC + jitter}s\n")
            time.sleep(POLL_INTERVAL_SEC + jitter)

    except KeyboardInterrupt:
        print("\nPOLL: interrupted (Ctrl+C). Saving state…")
        state["processed_ids"] = list(processed)
        _save_state(state)
        print("POLL: end")

if __name__ == "__main__":
    main()
