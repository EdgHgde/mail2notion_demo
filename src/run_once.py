# src/run_once.py
from __future__ import annotations
from .config import GOOGLE_CREDENTIALS_FILE, GMAIL_SEARCH_QUERY, GMAIL_PROCESSED_LABEL
from .gmail_client import (
    load_creds, get_service, search_messages, get_message,
    extract_text_from_message, add_label_processed, extract_urls_from_message, extract_email_dates
)
from .datetime_utils import choose_best_date
from .formatter import render_markdown, make_filename
from .io_utils import write_markdown
import re, time, socket
from rich import print

# 🔌 모든 소켓 기본 타임아웃(초) — Google API/HTTP 통째로 박음
socket.setdefaulttimeout(30)

MIN_BODY_LEN = 120
OVERALL_BUDGET_SEC = 180    # 전체 실행 3분 넘기면 종료
PER_MESSAGE_BUDGET_SEC = 60 # 메일 하나 처리에 60초 넘기면 스킵

def _strip_invisibles(s: str) -> str:
    """제로폭/제어문자 정리 + 개행 정돈"""
    if s is None:
        return ""
    s = re.sub(r"[\u200b-\u200f\u2028\u2029\u2060]+", "", s)  # zero-width 등 제거
    s = s.replace("\r\n", "\n").replace("\r", "\n")           # 개행 통일
    return s.strip()

def main():
    start_all = time.monotonic()
    print("[green]RUN: start[/green]")

    creds = load_creds(GOOGLE_CREDENTIALS_FILE)
    print("[green]GMAIL: creds loaded[/green]")
    svc = get_service(creds)
    print("[green]GMAIL: service ready[/green]")

    print(f"[green]GMAIL: search -> '{GMAIL_SEARCH_QUERY}'[/green]")
    # 최대 출력수 제한.
    ids = search_messages(svc, GMAIL_SEARCH_QUERY, max_results=10)
    print(f"[green]GMAIL: {len(ids)} message(s) found[/green]")

    if not ids:
        print("[yellow]No messages matched query.[/yellow]")
        return

    for msg_id in ids:
        if time.monotonic() - start_all > OVERALL_BUDGET_SEC:
            print("[red]WATCHDOG: overall time budget exceeded; abort.[/red]")
            break

        msg_start = time.monotonic()
        print(f"[cyan]MSG {msg_id[:8]}: start[/cyan]")

        try:
            msg = get_message(svc, msg_id)
            print(f"[cyan]MSG {msg_id[:8]}: fetched[/cyan]")
        except Exception as e:
            print(f"[red]MSG {msg_id[:8]}: fetch failed -> {e}[/red]")
            continue

        headers = {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}
        subject = headers.get("subject", "(no subject)")
        sender = headers.get("from", "(unknown sender)")
        print(f"[cyan]MSG {msg_id[:8]}: subject[/cyan] {subject} — {sender}")

        try:
            raw = _strip_invisibles(extract_text_from_message(svc, msg))
            print(f"[cyan]MSG {msg_id[:8]}: raw extracted ({len(raw)} chars)[/cyan]")
        except Exception as e:
            print(f"[red]MSG {msg_id[:8]}: extract failed -> {e}[/red]")
            continue

        preview = raw.replace("\n", " ")[:300]
        print(f"[white]raw preview:[/white] {preview}…")

        # raw data를 output에 markdown 형식으로 출력하는 코드
        # write_markdown(f"RAW_{make_filename(msg['id']).replace('.md','.txt')}", raw)

        # 날짜 후보
        try:
            dt_email_hdr, internal_ms = extract_email_dates(msg)
            print(f"[cyan]MSG {msg_id[:8]}: date candidates[/cyan] email={bool(dt_email_hdr)} gmail={bool(internal_ms)}")
        except Exception as e:
            dt_email_hdr, internal_ms = (None, None)
            print(f"[yellow]MSG {msg_id[:8]}: email dates parse fail -> {e}[/yellow]")

        best_date_display, best_src = "", "unknown"

        # 본문 보강
        composed_text = raw
        body_only = raw.split("\n\n", 1)[-1] if "\n\n" in raw else raw

        if len(body_only.strip()) < MIN_BODY_LEN:
            print(f"[blue]MSG {msg_id[:8]}: body short -> try article fetch[/blue]")
            try:
                from .article_fetcher import fetch_article_markdown  # 👈 여기서 임포트
            except Exception as e:
                print(f"[yellow]MSG {msg_id[:8]}: article_fetcher import fail -> {e}[/yellow]")
                fetch_article_markdown = None
            
            urls = extract_urls_from_message(msg)
            print(f"[blue]MSG {msg_id[:8]}: {len(urls)} url(s) found[/blue]")

            for u in urls[:3]:
                if time.monotonic() - msg_start > PER_MESSAGE_BUDGET_SEC:
                    print(f"[red]WATCHDOG: MSG {msg_id[:8]} budget exceeded; skipping.[/red]")
                    break
                print(f"[blue]MSG {msg_id[:8]}: fetch article {u}[/blue]")
                try:
                    art = fetch_article_markdown(u)
                except Exception as e:
                    print(f"[yellow]MSG {msg_id[:8]}: article fetch error -> {e}[/yellow]")
                    art = None
                if art:
                    title2, md_article, pub_kst = art
                    composed_text += f"\n\n[링크 기사] {u}\n\n{md_article}"
                    print(f"[green]MSG {msg_id[:8]}: article ok -> {title2}[/green]")
                    if pub_kst:
                        best_date_display, best_src = pub_kst, "article"
                    break

        # 날짜 확정
        if not best_date_display:
            disp, src = choose_best_date(None, dt_email_hdr, internal_ms)
            best_date_display, best_src = disp, src
        header_line = f"[DETECTED_DATE_KST:{best_date_display or '미확인'}|SOURCE:{best_src}]"
        composed_text = header_line + "\n" + composed_text
        print(f"[cyan]MSG {msg_id[:8]}: date -> {best_date_display or '미확인'} ({best_src})[/cyan]")

        # LLM
        if time.monotonic() - msg_start > PER_MESSAGE_BUDGET_SEC:
            print(f"[red]WATCHDOG: MSG {msg_id[:8]} budget exceeded before LLM; skipping.[/red]")
            continue

        try:
            print(f"[cyan]MSG {msg_id[:8]}: LLM start[/cyan]")
            md = render_markdown(composed_text, debug_tag=msg_id[:8])
            print(f"[cyan]MSG {msg_id[:8]}: LLM done[/cyan]")
        except Exception as e:
            print(f"[red]MSG {msg_id[:8]}: LLM failed -> {e}[/red]")
            continue

        outpath = write_markdown(make_filename(msg_id), md)
        print(f"[green]MSG {msg_id[:8]}: saved -> {outpath}[/green]")

        if GMAIL_PROCESSED_LABEL:
            try:
                add_label_processed(svc, msg_id, GMAIL_PROCESSED_LABEL)
                print(f"[magenta]MSG {msg_id[:8]}: labeled {GMAIL_PROCESSED_LABEL}[/magenta]")
            except Exception as e:
                print(f"[yellow]MSG {msg_id[:8]}: label failed -> {e}[/yellow]")

        print(f"[cyan]MSG {msg_id[:8]}: end ({int(time.monotonic()-msg_start)}s)[/cyan]")

    print(f"[green]RUN: end (total {int(time.monotonic()-start_all)}s)[/green]")

if __name__ == "__main__":
    print("[bold green]BOOT:[/bold green] __main__ guard hit", flush=True)
    main()
