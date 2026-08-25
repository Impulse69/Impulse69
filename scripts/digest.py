#!/usr/bin/env python3
"""Build a dated AI-news digest, archive it, and refresh the README panel.

Pulls the last 24 hours of AI headlines from Google News RSS. If GEMINI_API_KEY
is set the headlines are written up by Gemini; without a key the digest is still
produced as a sourced headline roundup, so the job never depends on a secret.

Nothing is committed on a failed or empty fetch — a quiet day leaves no commit
rather than an empty one.
"""

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIGEST_DIR = ROOT / "digests"
README = ROOT / "README.md"

FEED = (
    "https://news.google.com/rss/search"
    "?q=artificial+intelligence+OR+software+engineering+when:1d&hl=en-US&gl=US&ceid=US:en"
)
MAX_HEADLINES = 12
UA = {"User-Agent": "Mozilla/5.0 (compatible; impulse69-digest/1.0)"}


def fetch(url, data=None, headers=None, timeout=45):
    req = urllib.request.Request(url, data=data, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def get_headlines():
    """Return [(title, source, link)] from Google News RSS, newest first."""
    try:
        raw = fetch(FEED)
    except (urllib.error.URLError, TimeoutError) as e:
        print(f"feed fetch failed: {e}", file=sys.stderr)
        return []

    try:
        root = ET.fromstring(raw)
    except ET.ParseError as e:
        print(f"feed parse failed: {e}", file=sys.stderr)
        return []

    out = []
    for item in root.iterfind(".//item"):
        title = (item.findtext("title") or "").strip()
        if not title:
            continue
        link = (item.findtext("link") or "").strip()
        source = (item.findtext("source") or "").strip()
        # Google News suffixes the outlet onto the title: "Headline - The Verge"
        if not source and " - " in title:
            title, source = title.rsplit(" - ", 1)
        out.append((title.strip(), source.strip(), link))
        if len(out) >= MAX_HEADLINES:
            break
    return out


def summarize(headlines):
    """Ask Gemini for a short write-up. Returns None if unavailable."""
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        return None

    listing = "\n".join(f"- {t} ({s})" for t, s, _ in headlines)
    prompt = (
        "Below are today's AI and software engineering headlines. Write a brief "
        "digest for a working developer: 3-5 short paragraphs covering what "
        "actually matters and why. No preamble, no headings, no bullet lists. "
        "Do not invent details beyond the headlines.\n\n" + listing
    )
    body = json.dumps({"contents": [{"parts": [{"text": prompt}]}]}).encode()

    for model in ("gemini-2.5-flash", "gemini-2.5-flash-lite"):
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent?key={urllib.parse.quote(key)}"
        )
        try:
            resp = json.loads(fetch(url, data=body, headers={"Content-Type": "application/json"}))
            parts = resp["candidates"][0]["content"]["parts"]
            text = "".join(p.get("text", "") for p in parts).strip()
            if text:
                print(f"summary generated with {model}")
                return text
        except Exception as e:  # noqa: BLE001 - any failure just falls through
            print(f"{model} failed: {e}", file=sys.stderr)
    return None


def write_digest(day, headlines, summary):
    DIGEST_DIR.mkdir(exist_ok=True)
    path = DIGEST_DIR / f"{day}.md"

    lines = [f"# AI & Engineering Digest — {day}", ""]
    if summary:
        lines += [summary, "", "---", ""]
    lines.append("## Headlines")
    lines.append("")
    for title, source, link in headlines:
        label = f"{title} — *{source}*" if source else title
        lines.append(f"- [{label}]({link})" if link else f"- {label}")
    lines += [
        "",
        "---",
        "",
        f"<sub>Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC "
        f"from Google News RSS{' · summary by Gemini' if summary else ''}.</sub>",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def update_readme(day, headlines):
    """Refresh the panel between the digest markers. No-op if markers absent."""
    if not README.exists():
        return False
    text = README.read_text(encoding="utf-8")
    start, end = "<!--START_SECTION:digest-->", "<!--END_SECTION:digest-->"
    if start not in text or end not in text:
        print("digest markers not found in README", file=sys.stderr)
        return False

    count = len(list(DIGEST_DIR.glob("*.md")))
    lead = headlines[0][0] if headlines else ""
    if len(lead) > 110:
        lead = lead[:107].rstrip() + "…"
    # Keep the panel valid inside a shields.io URL and a markdown table cell
    safe = lead.replace("|", "·")
    badge_day = day.replace("-", "--")

    panel = f"""{start}
<div align="center">

<img src="https://img.shields.io/badge/latest_digest-{badge_day}-DC143C?style=for-the-badge&labelColor=0D1117" alt="Latest digest {day}" />
<img src="https://img.shields.io/badge/archived_days-{count}-0D1117?style=for-the-badge&labelColor=0D1117" alt="{count} archived days" />

**{safe}**

<sub><a href="digests/{day}.md">read today's digest</a> · <a href="digests/">browse the archive</a> · rebuilt every morning by GitHub Actions</sub>

</div>
{end}"""

    text = re.sub(
        re.escape(start) + r".*?" + re.escape(end), lambda _: panel, text, flags=re.S, count=1
    )
    README.write_text(text, encoding="utf-8")
    return True


def main():
    headlines = get_headlines()
    if not headlines:
        print("no headlines retrieved — skipping today (no commit)")
        return 0

    day = f"{datetime.now(timezone.utc):%Y-%m-%d}"
    path = write_digest(day, headlines, summarize(headlines))
    update_readme(day, headlines)
    print(f"wrote {path.relative_to(ROOT)} with {len(headlines)} headlines")
    return 0


if __name__ == "__main__":
    sys.exit(main())
