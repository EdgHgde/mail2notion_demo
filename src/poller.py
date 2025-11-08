# src/poller.py
from __future__ import annotations
import os, re, json, time, random, socket
from rich import print

from .config import GOOGLE_CREDENTIALS_FILE, GMAIL_SEARCH_QUERY
from .gmail_client import (
    load_creds, get_service, search_messages, get_message,
    extract_text_from_message, extract_urls_from_message, extract_email_dates
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
# 허용 티커(옵션). 비워두면 제한 없음. 예) export ALLOWED_TICKERS="NVDA,PLTR,TSLA"
ALLOWED_TICKERS = {
    t.strip().upper()
    for t in os.getenv("ALLOWED_TICKERS", "").split(",")
    if t.strip()
}
# ----------------------------------

socket.setdefaulttimeout(SOCKET_TIMEOUT)

# ---- 제목 선두 티커 추출 (SA 패턴 전용) ----
# 예시) "NVDA: ...", "PLTR – ...", "TSLA — ...", "NVDA, PLTR: ..."
# 콜론/하이픈/대시(–—) 모두 허용, 선두에 쉼표로 여러 종목도 허용
_SUBJ_LEAD_SINGLE = re.compile(r"^\s*([A-Z]{1,5})\s*[:\-–—]\s")
_SUBJ_LEAD_MULTI  = re.compile(r"^\s*([A-Z ,/&-]{3,})\s*[:\-–—]\s")

def _tickers_from_subject_leading(subject: str) -> list[str]:
    # 1) "NVDA: ..." 와 같이 단일 티커 선두 케이스
    m = _SUBJ_LEAD_SINGLE.match(subject or "")
    if m:
        cands = {m.group(1).upper()}
    else:
        # 2) "NVDA, PLTR: ..." 같이 다중 티커 선두 케이스
        m2 = _SUBJ_LEAD_MULTI.match(subject or "")
        cands = set()
        if m2:
            chunk = m2.group(1)
            for tok in re.split(r"[,\s/&-]+", chunk):
                t = tok.strip().upper()
                if 1 < len(t) <= 5 and t.isalpha():
                    cands.add(t)

    # 허용 목록 필터
    if ALLOWED_TICKERS:
        cands = cands & ALLOWED_TICKERS
    return sorted(cands)

# ---- 유틸 ----
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
    """
    기존 processed_ids(메시지 단위) → processed_keys(메시지#티커 단위)로
    백워드 호환 마이그레이션.
    """
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                st = json.load(f)
        except Exception:
            st = {}
    else:
        st = {}

    # 마이그레이션
    if "processed_keys" not in st:
        st["processed_keys"] = []
    if "processed_ids" in st and st["processed_ids"]:
        # 과거 처리건들은 msg#ALL 로 표기해 재처리 방지(최소 침습)
        st["processed_keys"].extend([f"{mid}#ALL" for mid in st["processed_ids"]])
        st["processed_ids"] = []  # 더는 사용하지 않음
    # 중복 제거
    st["processed_keys"] = sorted(set(st["processed_keys"]))
    return st

def _save_state(st: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=2)

# ---- 핵심 처리 ----
def _process_one(svc, msg_id: str, processed_keys: set[str], state: dict) -> bool:
    """
    단일 메시지 처리.
    - SA 제목 선두의 티커만 기준으로 종목을 결정
    - 최소 1개라도 처리하면 True
    """
    try:
        msg = get_message(svc, msg_id)
        subject, sender = _hdr(msg)
        print(f"MSG {msg_id[:8]}: start • {subject} — {sender}")

        raw = _strip_invisibles(extract_text_from_message(svc, msg))
        print(f"MSG {msg_id[:8]}: raw extracted ({len(raw)} chars)")

        # 날짜 후보
        try:
            dt_email_hdr, internal_ms = extract_email_dates(msg)
            print(f"MSG {msg_id[:8]}: date candidates email={bool(dt_email_hdr)} gmail={bool(internal_ms)}")
        except Exception as e:
            dt_email_hdr, internal_ms = (None, None)
            print(f"[yellow]MSG {msg_id[:8]}: email dates parse fail -> {e}[/yellow]")

        # 본문 보강 (짧으면 링크 기사 합성 시도)
        composed_text = raw
        body_only = raw.split("\n\n", 1)[-1] if "\n\n" in raw else raw
        best_date_display, best_src = "", "unknown"

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

        # ---- SA 제목 선두에서만 티커 추출 ----
        tickers = _tickers_from_subject_leading(subject)
        print(f"MSG {msg_id[:8]}: tickers detected (subject-leading) -> {', '.join(tickers) if tickers else '(none)'}")
        if not tickers:
            # 선두에 티커가 없다면 이 메일은 스킵(원칙 강화)
            print(f"[yellow]MSG {msg_id[:8]}: no leading ticker in subject -> skip[/yellow]")
            return False

        any_done = False
        for ticker in tickers:
            key = f"{msg_id}#{ticker}"
            if key in processed_keys:
                print(f"MSG {msg_id[:8]}: skip {ticker} (already processed)")
                continue

            # 티커를 프롬프트 헤더에 이미 주입하려면 여기서 추가 가능:
            # text_for_llm = f"[TICKER:{ticker}]\n" + composed_text
            text_for_llm = composed_text

            print(f"MSG {msg_id[:8]}:{ticker}: LLM start")
            md = render_markdown(text_for_llm)  # ticker 인자 없음
            print(f"MSG {msg_id[:8]}:{ticker}: LLM done")

            outpath = write_markdown(make_filename(f"{msg_id}_{ticker}"), md)
            print(f"MSG {msg_id[:8]}:{ticker}: saved -> {outpath}")

            processed_keys.add(key)
            state["processed_keys"] = sorted(processed_keys)
            _save_state(state)
            any_done = True

        print(f"MSG {msg_id[:8]}: end")
        return any_done

    except Exception as e:
        print(f"[red]MSG {msg_id[:8]}: failed -> {e}[/red]")
        return False

# ---- 메인 루프 ----
def main():
    print("POLL: start")
    print(f"query: '{GMAIL_SEARCH_QUERY}' | interval={POLL_INTERVAL_SEC}s batch={POLL_BATCH}")
    print(f"ALLOWED_TICKERS = {sorted(ALLOWED_TICKERS) if ALLOWED_TICKERS else '(no limit)'}")

    # Gmail 준비
    print("Launching browser for Gmail OAuth…")
    creds = load_creds(GOOGLE_CREDENTIALS_FILE)
    print("GMAIL: creds loaded")
    svc = get_service(creds)
    print("GMAIL: service ready")

    state = _load_state()
    processed_keys = set(state.get("processed_keys", []))
    idle_backoff = POLL_INTERVAL_SEC

    try:
        while True:
            print("\nTICK: search…")
            ids = search_messages(svc, GMAIL_SEARCH_QUERY, max_results=POLL_BATCH)
            print(f"FOUND: {len(ids)} message(s)")

            # 메시지 단위 중복 제거는 의미가 약함 → processed_keys(메시지#티커)로 관리
            new_ids = ids  # 항상 시도하고, 내부에서 티커 단위로 스킵

            if not new_ids:
                # idle → 백오프 증가(+지터)
                sleep_s = min(IDLE_BACKOFF_MAX, int(idle_backoff * 1.5))
                jitter = random.randint(0, min(5, sleep_s))
                idle_backoff = sleep_s
                print(f"IDLE: no new messages → sleep {sleep_s + jitter}s")
                time.sleep(sleep_s + jitter)
                continue

            # 새/기존 메시지 모두에서 '미처리 티커'가 있으면 처리
            idle_backoff = POLL_INTERVAL_SEC  # 작업 발생 시 백오프 초기화
            for msg_id in new_ids:
                _process_one(svc, msg_id, processed_keys, state)
                # processed_keys는 함수 내부에서 갱신/저장됨
                processed_keys = set(state.get("processed_keys", []))

            # 다음 사이클: 기본 인터벌(+지터) 대기
            jitter = random.randint(0, 3)
            print(f"SLEEP: {POLL_INTERVAL_SEC + jitter}s\n")
            time.sleep(POLL_INTERVAL_SEC + jitter)

    except KeyboardInterrupt:
        print("\nPOLL: interrupted (Ctrl+C). Saving state…")
        state["processed_keys"] = sorted(processed_keys)
        _save_state(state)
        print("POLL: end")

if __name__ == "__main__":
    main()
