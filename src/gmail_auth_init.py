from __future__ import annotations
from .config import GOOGLE_CREDENTIALS_FILE
from .gmail_client import load_creds, get_service

def main():
    creds = load_creds(GOOGLE_CREDENTIALS_FILE)
    svc = get_service(creds)
    profile = svc.users().getProfile(userId="me").execute()
    print("Authenticated as:", profile.get("emailAddress"))

if __name__ == "__main__":
    main()
