"""Render a UAT transcript (uat/mock_days.py output) as a phone-friendly chat page.

python uat/render_html.py OUT_DIR      # reads OUT_DIR/transcript.json, writes index.html
"""

import base64
import html
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
LONG = 700
STATE_LABEL = {
    "READ": "read",
    "DELIVERED": "delivered",
    "SENT": "sent",
    "ACCEPTED": "accepted",
    "UNKNOWN": "delivery unknown",
    "FAILED": "failed",
}
DAYS = {
    "2026-10-01": (
        "Thu 1 Oct 2026",
        "Trading day: onboarding, morning report, 09:12 update, TEXT, feedback, a correction",
    ),
    "2026-10-02": ("Fri 2 Oct 2026", "NSE holiday (Gandhi Jayanti): nothing should be sent"),
    "2026-10-05": (
        "Mon 5 Oct 2026",
        "Feed down all morning, and more than 24 hours since your last message",
    ),
}

CSS = """
/* Layout: one reading column; each day is a chat log with behind-the-scenes notes inline. */
:root {
  --bg: #f4f6f7; --surface: #ffffff; --ink: #172b3a; --muted: #52616b; --line: #dfe5ea;
  --bot: #ffffff; --me: #dcefe9; --accent: #0b625c; --navy: #244a63; --warn: #9c2727;
  --note: #e9eef2;
  --font-body: "IBM Plex Sans", "Segoe UI", system-ui, sans-serif;
  --font-mono: "IBM Plex Mono", ui-monospace, "SFMono-Regular", Menlo, monospace;
}
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  --bg: #0f1a22; --surface: #16242e; --ink: #e6edf2; --muted: #9fb0bc; --line: #26394a;
  --bot: #1b2c38; --me: #123d39; --accent: #5cc3b6; --navy: #8fb8d4; --warn: #f08a8a;
  --note: #1d2f3c; color-scheme: dark } }
:root[data-theme="dark"] {
  --bg: #0f1a22; --surface: #16242e; --ink: #e6edf2; --muted: #9fb0bc; --line: #26394a;
  --bot: #1b2c38; --me: #123d39; --accent: #5cc3b6; --navy: #8fb8d4; --warn: #f08a8a;
  --note: #1d2f3c; color-scheme: dark }
body { background: var(--bg); color: var(--ink); font: 15px/1.5 var(--font-body);
  padding-inline: 16px; padding-block: 24px 48px; }
.wrap { max-width: 680px; margin: 0 auto; display: grid; gap: 28px; min-width: 0; }
.wrap > * { min-width: 0; }
h1 { font-size: 1.6rem; line-height: 1.2; margin: 0; text-wrap: balance; }
h2 { font-size: 1.15rem; margin: 0; text-wrap: balance; }
p { margin: 0; }
.eyebrow { font: 600 0.72rem/1 var(--font-mono); letter-spacing: 0.08em; text-transform: uppercase;
  color: var(--warn); }
.lede { color: var(--muted); max-width: 62ch; }
.panel { background: var(--surface); border: 1px solid var(--line); border-radius: 10px;
  padding: 16px; display: grid; gap: 10px; }
.tasks { margin: 0; padding-left: 1.3em; display: grid; gap: 8px; }
.tasks li::marker { font-family: var(--font-mono); color: var(--accent); }
.small { font-size: 0.85rem; color: var(--muted); }
.day { display: grid; gap: 10px; }
.dayhead { display: grid; gap: 2px; border-bottom: 2px solid var(--navy); padding-bottom: 6px; }
.log { display: flex; flex-direction: column; gap: 8px; }
.msg { max-width: 88%; min-width: 0; padding: 8px 10px 6px; border-radius: 10px;
  border: 1px solid var(--line); background: var(--bot); align-self: flex-start;
  display: grid; gap: 4px; }
.msg.me { background: var(--me); align-self: flex-end; }
.who { font: 600 0.7rem/1 var(--font-mono); letter-spacing: 0.05em; color: var(--muted);
  text-transform: uppercase; }
.body { white-space: pre-wrap; overflow-wrap: anywhere; font-size: 0.92rem; }
.meta { font: 0.72rem/1.2 var(--font-mono); color: var(--muted); justify-self: end;
  font-variant-numeric: tabular-nums; }
.note { align-self: center; max-width: 92%; background: var(--note); color: var(--muted);
  border-radius: 6px; padding: 5px 9px; font: 0.76rem/1.4 var(--font-mono); text-align: center; }
.note b { color: var(--ink); font-weight: 600; }
.note.held { color: var(--warn); }
details summary { cursor: pointer; color: var(--accent); font-size: 0.85rem; }
details[open] summary { margin-bottom: 4px; }
.media { min-width: 0; }
.media img { border-radius: 6px; border: 1px solid var(--line); display: block;
  max-width: 100%; height: auto; }
.doc { display: flex; gap: 10px; align-items: center; padding: 8px; border-radius: 6px;
  border: 1px solid var(--line); background: var(--surface); }
.doc .badge { font: 700 0.7rem/1 var(--font-mono); color: var(--surface); background: var(--warn);
  border-radius: 4px; padding: 6px 5px; }
.doc a { color: var(--accent); font-weight: 600; overflow-wrap: anywhere; }
.side { border-left: 3px solid var(--line); padding-left: 12px; display: grid; gap: 8px; }
ul.jobs { margin: 0; padding-left: 1.1em; font: 0.8rem/1.6 var(--font-mono); color: var(--muted); }
@page { size: A4; margin: 14mm 12mm; }
@media print { body { background: #fff; } .msg, .note, .panel { break-inside: avoid; }
  .wrap { max-width: none; } }
a:focus-visible, summary:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
"""


def ist(ts: str) -> str:
    return datetime.fromisoformat(ts).astimezone(IST).strftime("%H:%M IST")


EMBED: dict[str, str] = {}  # media path -> data: URI (standalone export only)
EXPAND = False  # standalone export shows every message in full (prints cleanly)


def dl(name: str) -> str:
    """download= only in the standalone file; the online viewer ignores download links."""
    return f' download="{html.escape(name)}"' if EMBED else ""


def src(path: str) -> str:
    return EMBED.get(path, path)


def bubble(ev: dict) -> str:
    e = html.escape
    if ev["dir"] == "system":
        held = " held" if ev["text"].startswith("NOT SENT") else ""
        return (
            f'<div class="note{held}"><b>Behind the scenes · {ist(ev["t"])}</b><br>'
            f"{e(ev['text'])}</div>"
        )
    me = ev["dir"] == "in"
    who = (
        "You (MOCK number)"
        if me and ev["thread"] == "vikas"
        else ("Unknown number (MOCK)" if me else "Research desk")
    )
    parts = [f'<div class="who">{who}</div>']
    media = ev.get("media")
    text = ev.get("text") or ""
    if media and media["kind"] == "image":
        parts.append(
            f'<div class="media"><img src="{e(src(media["file"]))}" '
            f'alt="{e(text.splitlines()[-1] if text else "chart")}" loading="lazy"></div>'
        )
    elif media:
        name = media["file"].split("/")[-1]
        parts.append(
            f'<div class="doc"><span class="badge">PDF</span>'
            f'<a href="{e(src(media["file"]))}"{dl(name)} target="_blank" '
            f'rel="noopener">{e(name)}</a></div>'
        )
    if len(text) > LONG and not EXPAND:
        first = "\n".join(text.splitlines()[:3])
        parts.append(
            f'<div class="body">{e(first)}</div><details><summary>Show full message '
            f'({len(text)} characters)</summary><div class="body">{e(text)}</div></details>'
        )
    else:
        parts.append(f'<div class="body">{e(text)}</div>')
    status = "" if me else f" · {STATE_LABEL.get(ev.get('state', ''), ev.get('state', '').lower())}"
    parts.append(f'<div class="meta">{ist(ev["t"])}{status}</div>')
    return f'<div class="msg{" me" if me else ""}">{"".join(parts)}</div>'


def render(out: Path, standalone: bool = False) -> None:
    data = json.loads((out / "transcript.json").read_text())
    by_day: dict[str, list] = {}
    for ev in data["events"]:
        day = datetime.fromisoformat(ev["t"]).astimezone(IST).date().isoformat()
        by_day.setdefault(day, []).append(ev)
    days_html = []
    for day, events in by_day.items():
        title, sub = DAYS.get(day, (day, ""))
        mine = "".join(bubble(ev) for ev in events if ev["thread"] == "vikas")
        other = [ev for ev in events if ev["thread"] == "stranger"]
        side = (
            ""
            if not other
            else '<div class="side"><p class="small">Meanwhile, a number that was never invited '
            'writes in:</p><div class="log">' + "".join(bubble(ev) for ev in other) + "</div></div>"
        )
        days_html.append(
            f'<section class="day"><div class="dayhead"><h2>{title}</h2>'
            f'<p class="small">{html.escape(sub)}</p></div>'
            f'<div class="log">{mine}</div>{side}</section>'
        )
    jobs = "".join(f"<li>{html.escape(j)}</li>" for j in data["jobs"])
    page = f"""<title>Research Desk UAT</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600;700&family=IBM+Plex+Sans:wght@400;600;700&display=swap">
<style>{CSS}</style>
<main class="wrap">
  <header style="display:grid;gap:8px">
    <p class="eyebrow">MOCK data · simulated clock · nothing was sent</p>
    <h1>Research Desk UAT</h1>
    <p class="lede">Three simulated days of the WhatsApp research desk, run end to end through
    the real code: invite, onboarding, scheduler, worker, PDF and chart, delivery rules,
    receipts and a correction. Only Meta's server is faked. Grey notes show what happened
    behind the scenes; the customer never sees them.</p>
  </header>
  <section class="panel" aria-labelledby="d07">
    <h2 id="d07">Your D07 check</h2>
    <p class="small">Use only the chat below (open the PDF and chart as you would on your phone).
    No dashboard. Reply in our chat with what you found for each:</p>
    <ol class="tasks">
      <li>The session path that would turn bullish, its trigger, and what invalidates it.</li>
      <li>The focus stocks for the day, and why each one was shortlisted.</li>
      <li>One piece of data the report could not give you, and the stated reason.</li>
      <li>The source and the time for any one number you rely on.</li>
    </ol>
    <p class="small">Also note anything that confused you, felt too long, or would make you
    distrust the desk. That feeds the next round.</p>
  </section>
  {"".join(days_html)}
  <section class="panel">
    <h2>Run record</h2>
    <ul class="jobs">{jobs}</ul>
    <p class="small">Generated by <code>uat/mock_days.py</code> and
    <code>uat/render_html.py</code>. Versions: smoke stack (Python 3.13.14, PostgreSQL 16.14),
    not yet the pinned 3.13.15 / 17.11.</p>
  </section>
</main>
"""
    if not standalone:
        (out / "index.html").write_text(page)
        return
    doc = (
        '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        + page.replace("<main", "</head>\n<body>\n<main", 1)
        + "</body>\n</html>\n"
    )
    (out / "D07-mock-uat.html").write_text(doc)


def standalone(out: Path) -> None:
    global EXPAND
    mime = {".png": "image/png", ".pdf": "application/pdf"}
    for f in sorted((out / "media").iterdir()):
        data = base64.b64encode(f.read_bytes()).decode()
        EMBED[f"media/{f.name}"] = f"data:{mime[f.suffix]};base64,{data}"
    EXPAND = True
    render(out, standalone=True)


if __name__ == "__main__":
    if "--standalone" in sys.argv:
        standalone(Path(sys.argv[1]))
    else:
        render(Path(sys.argv[1]))
