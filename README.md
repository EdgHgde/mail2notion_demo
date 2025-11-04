# mail2notion_demo
**Gmail → LLM 요약 → Notion 자동 업로드 파이프라인(추후 작업 예정)**

---

## 🧭 프로젝트 개요

**Mail2Notion Demo**는  
Seeking Alpha 등의 뉴스 메일을 자동으로 수집하고,  
OpenAI 모델을 이용해 마크다운 형식의 요약을 생성한 뒤,  
자동으로 Notion 데이터베이스에 업로드하는 자동화 시스템입니다.

전체 구조는 다음과 같습니다:
'''
Gmail Inbox
↓ (Google API)
mail2notion_demo
├── gmail_client.py   # Gmail API 통신
├── run_once.py       # 메인 파이프라인 (단발 실행)
├── formatter.py      # LLM 요약 생성
├── article_fetcher.py# 뉴스 링크 본문 크롤링
├── notion_uploader.py# Notion API 업로드
├── io_utils.py       # 파일 입출력
├── datetime_utils.py # 날짜 추출 및 변환
├── config.py         # 환경 변수 로드
└── templates/
└── news_prompt.txt  # LLM 프롬프트 템플릿
'''

---

## ⚙️ 설치 및 환경 구성

### 1️⃣ 가상환경 생성 및 패키지 설치

```bash
python3 -m venv myvenv
source myvenv/bin/activate
pip install -r requirements.txt

.env 파일 예시:
# Google API
GOOGLE_CREDENTIALS_FILE=credentials.json
GMAIL_PROCESSED_LABEL=processed-by-EdgH
GMAIL_SEARCH_QUERY=(from:(account@seekingalpha.com "SA Breaking News") AND (subject:NVDA OR subject:PLTR OR subject:TSLA))

# OpenAI
OPENAI_API_KEY=sk-xxxxxx
OPENAI_MODEL=gpt-4o-mini
OPENAI_BASE_URL=https://api.openai.com/v1

# Notion
NOTION_TOKEN=secret_xxxxx
NOTION_DATABASE_ID=xxxxxxxxxxxxxxxxxxxx

## 🚀 실행 방법

Mail2Notion Demo는 두 가지 실행 방식이 있습니다:

---

```bash
# 가상환경 활성화
source myvenv/bin/activate

# 단발 실행
python -m src.run_once

# poller 실행 (주기: 5분 간격)
python -m src.poller
