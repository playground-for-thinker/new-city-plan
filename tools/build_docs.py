"""도움말 PDF 만들기 — docs/user_manual.pdf, docs/supplement.pdf

본문(docs/*.md)이나 기준정보를 바꾼 뒤에는 이 스크립트를 다시 실행해 PDF를 새로 만든다.

    uv run python tools/build_docs.py

Chromium으로 인쇄하므로 처음 한 번 `uv run playwright install chromium`이 필요하다. 한글 글꼴(나눔고딕 등)이 설치되어 있어야 한다.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import date
from pathlib import Path

import markdown
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from newtown import docs  # noqa: E402

CSS = """
@page { size: A4; margin: 18mm 15mm 18mm 15mm; }
* { box-sizing: border-box; }
body { font-family: "NanumBarunGothic", "NanumGothic", "Noto Sans CJK KR", "Malgun Gothic", sans-serif;
       font-size: 10pt; line-height: 1.6; color: #1a1a19; word-break: keep-all; }
h1 { font-size: 20pt; line-height: 1.3; margin: 0 0 4pt; }
.meta { color: #52514e; font-size: 9pt; margin: 0 0 14pt; padding-bottom: 8pt; border-bottom: 1.5pt solid #1a1a19; }
h2 { font-size: 14pt; margin: 20pt 0 6pt; padding-bottom: 3pt; border-bottom: 0.75pt solid #c9c8c2; break-after: avoid; }
h3 { font-size: 11.5pt; margin: 14pt 0 4pt; break-after: avoid; }
p, ul, ol { margin: 5pt 0; }
li { margin: 2pt 0; }
table { border-collapse: collapse; width: 100%; margin: 6pt 0 10pt; font-size: 8.3pt; line-height: 1.4; }
th, td { border: 0.5pt solid #b0b7c0; padding: 3pt 4pt; vertical-align: top; text-align: left; }
th { background: #e9eef4; font-weight: 700; }
tr { break-inside: avoid; }
thead { display: table-header-group; }
td.num, th.num { text-align: right; white-space: nowrap; }
pre { background: #f4f4f1; border: 0.5pt solid #d8d7d1; border-radius: 3pt; padding: 7pt 9pt; font-size: 8.6pt; line-height: 1.5;
      white-space: pre-wrap; break-inside: avoid; }
code { font-family: "NanumGothicCoding", "D2Coding", monospace; font-size: 0.93em; }
p code, li code, td code { background: #f0efec; padding: 0 2pt; border-radius: 2pt; }
figure { margin: 8pt 0 12pt; break-inside: avoid; text-align: center; }
figure img { max-width: 100%; max-height: 190mm; border: 0.5pt solid #c9c8c2; }
figcaption { font-size: 8.5pt; color: #52514e; margin-top: 3pt; }
a { color: #1c5cab; text-decoration: none; word-break: break-all; }
"""

FOOTER = ('<div style="width:100%; font-size:8px; color:#777; padding:0 15mm; display:flex; justify-content:space-between;'
          ' font-family: NanumGothic, sans-serif;"><span>{title}</span>'
          '<span><span class="pageNumber"></span> / <span class="totalPages"></span></span></div>')


def to_html(name: str) -> str:
    body = markdown.markdown(docs.render(name), extensions=["tables", "fenced_code", "sane_lists"])
    # 그림은 설명과 함께 figure 로 감싼다
    body = re.sub(r'<p><img alt="([^"]*)" src="([^"]+)" ?/?></p>', r'<figure><img src="\2"><figcaption>\1</figcaption></figure>', body)
    # 숫자만 든 칸은 오른쪽 정렬
    body = re.sub(r"<td>(\s*[-+]?[\d,]+(?:\.\d+)?%?\s*)</td>", r'<td class="num">\1</td>', body)
    meta = f'<p class="meta">작성 기준일 {date.today().isoformat()} · 저장소 playground-for-thinker/new-city-plan</p>'
    body = body.replace("</h1>", "</h1>" + meta, 1)
    base = docs.DOCS_DIR.resolve().as_uri() + "/"
    return f'<!doctype html><html lang="ko"><head><meta charset="utf-8"><base href="{base}"><style>{CSS}</style></head><body>{body}</body></html>'


def main() -> None:
    build_dir = docs.DOCS_DIR / "_build"
    build_dir.mkdir(exist_ok=True)
    info = {}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for name, title in docs.DOCUMENTS.items():
            html_path = build_dir / f"{name}.html"
            html_path.write_text(to_html(name), encoding="utf-8")
            page = browser.new_page()
            page.goto(html_path.resolve().as_uri(), wait_until="networkidle")
            page.pdf(path=str(docs.pdf_path(name)), format="A4", print_background=True, display_header_footer=True,
                     header_template="<span></span>", footer_template=FOOTER.format(title=f"신도시 개발사업 타당성 분석 시스템 — {title}"),
                     margin={"top": "18mm", "bottom": "18mm", "left": "15mm", "right": "15mm"})
            page.close()
            info[name] = docs.content_hash(name)
            print(f"{docs.pdf_path(name).name}: {docs.pdf_path(name).stat().st_size / 1024:,.0f} KB")
        browser.close()
    (docs.DOCS_DIR / "build_info.json").write_text(json.dumps(info, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
