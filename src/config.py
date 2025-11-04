import os
from dotenv import load_dotenv

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "").strip() or None
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")

GOOGLE_CREDENTIALS_FILE = os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials.json")
GMAIL_SEARCH_QUERY = 'from:(account@seekingalpha.com "SA Breaking News")'
GMAIL_PROCESSED_LABEL = os.getenv("GMAIL_PROCESSED_LABEL", "")

OUTPUT_DIR = os.getenv("OUTPUT_DIR", "./out")
