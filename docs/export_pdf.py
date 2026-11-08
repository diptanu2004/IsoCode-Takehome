# /// script
# requires-python = ">=3.12"
# dependencies = ["markdown>=3.7"]
# ///
"""Export the design document to PDF: Markdown -> styled HTML -> headless Chrome or Edge.

    uv run docs/export_pdf.py            # writes docs/Settle-TDD.pdf

Links to files in the repository are rewritten to their GitHub URLs, since a PDF has no repo.
"""

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import markdown

DOCS = Path(__file__).resolve().parent
REPO_URL = "https://github.com/diptanu2004/IsoCode-Takehome"
BROWSERS = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "google-chrome",
    "chromium",
)

CSS = """
@page { size: A4; margin: 16mm 15mm 18mm; }
* { box-sizing: border-box; }
body { font: 10.2pt/1.5 "Segoe UI", system-ui, -apple-system, sans-serif; color: #101828; }
h1 { font-size: 21pt; margin: 0 0 6pt; letter-spacing: -0.01em; }
h2 { font-size: 14.5pt; margin: 20pt 0 6pt; padding-top: 6pt; border-top: 1px solid #e4e7ec;
     break-after: avoid; }
h3 { font-size: 11.5pt; margin: 14pt 0 4pt; break-after: avoid; }
p, ul, ol { margin: 0 0 7pt; }
li { margin: 1.5pt 0; }
a { color: #2a4bb8; text-decoration: none; }
code { font: 8.8pt Consolas, monospace; background: #f2f4f7; padding: 0.5pt 3pt; border-radius: 3px; }
pre { background: #f7f8fa; border: 1px solid #e4e7ec; border-radius: 6px; padding: 7pt 9pt;
      font-size: 8.5pt; white-space: pre-wrap; break-inside: avoid; }
pre code { background: none; padding: 0; }
table { border-collapse: collapse; width: 100%; margin: 4pt 0 10pt; font-size: 8.8pt;
        break-inside: auto; }
tr { break-inside: avoid; }
th, td { border: 1px solid #d0d5dd; padding: 3.5pt 5pt; vertical-align: top; text-align: left; }
th { background: #f2f4f7; font-weight: 600; }
td ul { margin: 0; padding-left: 12pt; }
hr { border: 0; border-top: 1px solid #e4e7ec; margin: 12pt 0; }
img { max-width: 100%; border: 1px solid #e4e7ec; border-radius: 6px; margin: 4pt 0 2pt; }
blockquote { margin: 0 0 8pt; padding: 4pt 10pt; border-left: 3px solid #d0d5dd; color: #5d6679; }
"""


def repo_links(text: str) -> str:
    """Point links at files in the repository to GitHub; keep in-page anchors and images."""

    def fix(match: re.Match) -> str:
        label, target = match.group(1), match.group(2)
        if target.startswith(("http", "#", "mailto:")) or match.group(0).startswith("!"):
            return match.group(0)
        path = (DOCS / target.split("#")[0]).resolve().relative_to(DOCS.parent).as_posix()
        anchor = "#" + target.split("#")[1] if "#" in target else ""
        return f"[{label}]({REPO_URL}/blob/main/{path}{anchor})"

    return re.sub(r"!?\[([^\]]+)\]\(([^)\s]+)\)", fix, text)


def browser() -> str:
    for candidate in BROWSERS:
        found = shutil.which(candidate) or (candidate if Path(candidate).is_file() else None)
        if found:
            return found
    sys.exit("No Chrome or Edge found to print the PDF.")


def main() -> None:
    source = DOCS / "TDD.md"
    out = DOCS / "Settle-TDD.pdf"
    body = markdown.markdown(
        repo_links(source.read_text(encoding="utf-8")),
        extensions=["tables", "fenced_code", "toc", "sane_lists"],
    )
    page = (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        f"<title>Settle: Technical Design Document</title><style>{CSS}</style></head>"
        f"<body>{body}</body></html>"
    )
    with tempfile.TemporaryDirectory() as tmp:
        html = DOCS / "_export.html"  # next to the images, so relative paths resolve
        html.write_text(page, encoding="utf-8")
        try:
            subprocess.run(
                [
                    browser(),
                    "--headless=new",
                    "--disable-gpu",
                    f"--user-data-dir={tmp}",
                    "--no-pdf-header-footer",
                    f"--print-to-pdf={out}",
                    html.as_uri(),
                ],
                check=True,
                capture_output=True,
                timeout=120,
            )
        finally:
            html.unlink(missing_ok=True)
    print(f"Wrote {out} ({out.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
