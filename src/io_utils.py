import os, pathlib
from .config import OUTPUT_DIR

def ensure_out_dir():
    pathlib.Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)


def write_markdown(name: str, content: str) -> str:
    ensure_out_dir()
    path = os.path.join(OUTPUT_DIR, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path

def extract_title_from_md(md: str) -> str:
    if not md:
        return "핵심 이슈 요약"

    first_line = md.strip().splitlines()[0].strip()  # 📈 ... | ...
    title_part = first_line.split("|", 1)[0].strip() # 📈 ... (날짜 제거)
    return title_part
