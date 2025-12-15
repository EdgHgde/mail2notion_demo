from __future__ import annotations
import os, datetime, hashlib
from openai import OpenAI
from .config import OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_MODEL
from .io_utils import write_markdown
from rich import print

# 템플릿 경로
_PROMPT_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "templates", "news_prompt.txt")
)

def _sha1(s: str) -> str:
    """내용 요약 확인용 SHA1 해시 (앞 10자리만)."""
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:10]

def load_prompt() -> str:
    """프롬프트 파일을 읽어 반환."""
    print(f"[blue]🧭 Using prompt:[/blue] {_PROMPT_PATH}")
    with open(_PROMPT_PATH, "r", encoding="utf-8") as f:
        prompt = f.read()
    print(f"[blue]🧾 prompt sha1:[/blue] {_sha1(prompt)}")
    return prompt

def _make_client() -> OpenAI:
    """OpenAI 클라이언트 초기화."""
    if OPENAI_BASE_URL:
        return OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL)
    return OpenAI(api_key=OPENAI_API_KEY)

def render_markdown(raw_text: str, debug_tag: str = "") -> str:
    """이메일 원문 + 템플릿을 LLM에 보내 마크다운 요약을 생성."""
    body = (raw_text or "").strip()
    if len(body) < 80:
        raise ValueError("Raw email content too short; aborting to avoid template echo.")

    # 1️⃣ 템플릿 로드
    instructions = load_prompt()

    # 2️⃣ 디버그용 프롬프트 덤프
    tag = debug_tag or datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    composed_preview = instructions + "\n\n[원문은 별도 message로 전달]\n"
    print(f"[blue]🧪 composed sha1:[/blue] {_sha1(composed_preview)}")

    # 3️⃣ LLM 호출
    client = _make_client()
    rsp = client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a precise financial news editor. "
                    "Use ONLY facts from the user's raw text. "
                    "If the raw text lacks details, say '원문 부족' and summarize only what is given. "
                    "Do NOT fabricate or reuse any prior sample text. "
                    "Output must be valid GitHub-Flavored Markdown."
                ),
            },
            {"role": "user", "content": instructions},  # 템플릿
            {"role": "user", "content": raw_text},       # 실제 원문
        ],
        # temperature=0.2, (gpt-5는 기본 temperature만 사용 가능)
        top_p=1.0,
    )
    return rsp.choices[0].message.content.strip()

def make_filename(msg_id: str) -> str:
    """출력 파일명 생성."""
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    safe = msg_id.replace("/", "_")
    return f"{ts}_{safe}.md"
