"""Convert a Markdown document to a styled A4 PDF using headless Chrome/Edge.

    python app/tools/md_to_pdf.py _doc/06_simulator_guide.md [out.pdf]

Needs `markdown` (pip) and Chrome or Edge installed."""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import markdown

CSS = """
@page { size: A4; margin: 16mm 15mm 16mm 15mm; }
body { font-family: "Segoe UI", Arial, sans-serif; font-size: 10.5pt; line-height: 1.45; color: #1d2329; }
h1 { font-size: 19pt; color: #1f3b57; border-bottom: 2px solid #1f3b57; padding-bottom: 4px; margin-top: 0; }
h2 { font-size: 14pt; color: #1f3b57; margin-top: 18px; break-after: avoid; }
h3 { font-size: 11.5pt; color: #24507a; margin: 14px 0 4px; break-after: avoid; }
p, li { orphans: 3; widows: 3; }
ul, ol { padding-left: 22px; margin: 4px 0; }
li { margin: 2px 0; }
code { font-family: Consolas, monospace; font-size: 9.5pt; background: #eef2f6; padding: 0 3px; border-radius: 3px; }
pre { background: #eef2f6; padding: 8px 10px; border-radius: 4px; font-size: 9pt; break-inside: avoid; }
pre code { background: none; padding: 0; }
table { border-collapse: collapse; margin: 6px 0 10px; font-size: 9.5pt; break-inside: avoid; }
th, td { border: 1px solid #c5ced8; padding: 3px 7px; text-align: left; vertical-align: top; }
th { background: #e3eaf2; }
blockquote { margin: 8px 0; padding: 6px 10px; background: #fff7e0; border-left: 4px solid #e0a800; }
blockquote p { margin: 0; }
hr { border: none; border-top: 1px solid #c5ced8; margin: 14px 0; }
strong { color: #10283f; }
"""

BROWSERS = [r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"]


def md_to_pdf(src: Path, out: Path) -> Path:
    body = markdown.markdown(src.read_text(encoding="utf-8"), extensions=["tables", "fenced_code", "sane_lists"])
    title = src.stem.replace("_", " ")
    html = f"<!doctype html><html><head><meta charset='utf-8'><title>{title}</title><style>{CSS}</style></head>" \
           f"<body>{body}</body></html>"
    browser = next((b for b in BROWSERS if Path(b).exists()), None) or shutil.which("chrome") or shutil.which("msedge")
    if not browser:
        raise RuntimeError("Chrome or Edge not found")
    with tempfile.TemporaryDirectory() as tmp:
        h = Path(tmp) / "doc.html"
        h.write_text(html, encoding="utf-8")
        subprocess.run([browser, "--headless=new", "--disable-gpu", "--no-first-run", f"--user-data-dir={tmp}/prof",
                        "--no-pdf-header-footer", f"--print-to-pdf={out.resolve()}", h.as_uri()],
                       check=True, capture_output=True, timeout=120)
    return out


if __name__ == "__main__":
    s = Path(sys.argv[1])
    o = Path(sys.argv[2]) if len(sys.argv) > 2 else s.with_suffix(".pdf")
    print(md_to_pdf(s, o))
