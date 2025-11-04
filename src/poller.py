from __future__ import annotations
import json, os
from .config import GOOGLE_CREDENTIALS_FILE, GMAIL_SEARCH_QUERY, GMAIL_PROCESSED_LABEL
from .gmail_client import load_creds, get_service, search_messages, get_message, extract_text_from_message, add_label_processed
from .formatter import render_markdown, make_filename
from .io_utils import write_markdown
from rich import print

STATE_FILE = ".state.json"

def load_state():
    if os.path.exists(STATE_FILE):
        return json.load(open(STATE_FILE, "r"))
    return {"processed_ids": []}


def save_state(st):
    json.dump(st, open(STATE_FILE, "w"))


def main():
    st = load_state()
    processed = set(st.get("processed_ids", []))

    creds = load_creds(GOOGLE_CREDENTIALS_FILE)
    svc = get_service(creds)

    ids = search_messages(svc, GMAIL_SEARCH_QUERY, max_results=30)
    new_ids = [i for i in ids if i not in processed]
    if not new_ids:
        print("[yellow]No new messages.[/yellow]")
        return

    for msg_id in new_ids:
        msg = get_message(svc, msg_id)
        
        # --- 로그 ---
        headers = {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}
        subject = headers.get("subject", "(no subject)")
        sender = headers.get("from", "(unknown sender)")
        print(f"[cyan]📩 Processing mail:[/cyan] [bold]{subject}[/bold] — {sender}")
        # ---     ---

        raw = extract_text_from_message(svc, msg)
        md = render_markdown(raw)
        fname = make_filename(msg_id)
        outpath = write_markdown(fname, md)
        print(f"[green]Saved -> {outpath}[/green]")
        if GMAIL_PROCESSED_LABEL:
            add_label_processed(svc, msg_id, GMAIL_PROCESSED_LABEL)
        processed.add(msg_id)

    st["processed_ids"] = list(processed)
    save_state(st)
    print(f"[blue]Done. Processed {len(new_ids)} new messages.[/blue]")

if __name__ == "__main__":
    main()
