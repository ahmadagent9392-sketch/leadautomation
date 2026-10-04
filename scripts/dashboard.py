#!/usr/bin/env python3
"""Local dashboard (Stage 8): one page that shows what needs Ahmad. Only on this PC (127.0.0.1).

Usage:
    python scripts/dashboard.py              # real data  -> open http://127.0.0.1:8765
    python scripts/dashboard.py --demo       # made-up businesses only (data/demo.db, run scripts/demo.py first)
    python scripts/dashboard.py --port 8800
Stop it with Ctrl+C.

The page reads the database; it changes nothing there. Two boxes start a background job with the same fixed
tool list as the morning run (scripts/daily.py ALLOWED_TOOLS: nothing that sends):
  - "Search an idea"   -> claude -p "/search-idea ..."
  - "Add from screen"  -> pasted text / a screenshot is saved in data/paste/, then claude -p "/add-from-screen FILE"
One background job at a time, never during the morning run.
"Send by hand" shows approved LinkedIn / Upwork / agency / referral messages with a Copy button.
Safety: listens on 127.0.0.1 only; every form needs a secret token made at start (other web sites cannot start a
job); the Host header must be 127.0.0.1 / localhost; the idea text is checked and never goes through a shell;
uploads: PNG/JPG, max 5 MB. All database text is HTML-escaped. The only script is the Copy button (CSP nonce).
"""
from __future__ import annotations

import argparse
import html
import json
import re
import secrets
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

import cards as cards_mod
import config_check
import daily
import db
import digest
import ideas as ideas_mod
import scout
import screen
import today as today_mod
from email.parser import BytesParser
from email.policy import HTTP

HOST = "127.0.0.1"
DEFAULT_PORT = 8765
IDEA_RE = re.compile(r"^[A-Za-z0-9 ,.'()/+\-]{3,200}$")   # no quotes, &, |, <, >, %, ^, ! (cmd.exe)
NONCE_RE = re.compile(r'<script nonce="([A-Za-z0-9_-]+)">')
GOOD_STATES = ("qualified", "draft_ready", "approved", "contacted", "replied", "meeting", "proposal", "won")
MAX_BODY = 4096
MAX_UPLOAD = screen.MAX_IMAGE + screen.MAX_TEXT * 4 + 64 * 1024   # screenshot + text + form overhead


def esc(text) -> str:
    return html.escape(str(text if text is not None else ""), quote=True)


def check_idea(text: str) -> str:
    """One clean line, or DeskError with the reason."""
    idea = " ".join((text or "").split())
    if not idea:
        raise db.DeskError("type an idea first")
    if not IDEA_RE.match(idea):
        raise db.DeskError("use 3-200 letters, numbers, spaces and , . ' ( ) / + - only")
    return idea


class App:
    """Settings + the background idea search. One per dashboard process."""

    def __init__(self, *, demo: bool = False, port: int = DEFAULT_PORT, logs: Path = daily.LOGS,
                 stop_file: Path = daily.STOP_FILE, open_store=None, desk_kwargs: dict | None = None,
                 runner=None, claude: str | None = None, paste_dir: Path | None = None) -> None:
        self.demo = demo
        self.port = port
        self.logs = Path(logs)
        self.stop_file = Path(stop_file)
        self.token = secrets.token_urlsafe(24)
        self._open_store = open_store or (lambda: db.open_store(demo=demo))
        self.desk_kwargs = desk_kwargs if desk_kwargs is not None else db.desk_options(demo)
        self.runner = runner
        self.claude = claude
        self.thread: threading.Thread | None = None
        self.paste_dir = Path(paste_dir) if paste_dir else screen.PASTE_DIR
        self.message = ""                 # one line shown at the top after a search request

    # ---------- search ----------
    def searching(self) -> dict | None:
        return daily.lock_info(self.logs, daily.SEARCH_LOCK_NAME)

    def _check_can_start(self) -> str:
        if self.demo:
            raise db.DeskError("this is off in demo mode")
        if daily.lock_info(self.logs, daily.LOCK_NAME):
            raise db.DeskError("the morning run is going now. Try again when it has finished.")
        if self.stop_file.exists():
            raise db.DeskError("data/STOP exists: everything is stopped")
        if self.searching():
            raise db.DeskError("another background job is going now. Wait for it to finish.")
        return self.claude or daily.find_claude()

    def start_job(self, kind: str, prompt: str, label: str, extra: dict, claude: str, wait: bool = False) -> None:
        """Runs one `claude -p PROMPT` in the background (fixed tool list, logged in logs/)."""
        try:
            daily.take_lock(self.logs, daily.SEARCH_LOCK_NAME, what=label)
        except daily.RunError:
            raise db.DeskError("another background job is going now. Wait for it to finish.") from None
        tz = db.local_tz()
        timeout = daily.settings(db.load_yaml("policy"))["timeout_minutes"]

        def job() -> None:
            try:
                kw = {"runner": self.runner} if self.runner else {}
                daily.run_claude(prompt, kind=kind, tz=tz, timeout_minutes=timeout, logs=self.logs, claude=claude,
                                 extra=extra, **kw)
            finally:
                daily.free_lock(self.logs, daily.SEARCH_LOCK_NAME)

        self.thread = threading.Thread(target=job, daemon=True)
        self.thread.start()
        if wait:
            self.thread.join()

    def start_search(self, idea_text: str, *, wait: bool = False) -> str:
        if self.demo:
            raise db.DeskError("search is off in demo mode")
        idea = check_idea(idea_text)
        claude = self._check_can_start()
        self.start_job("search", f"/search-idea {idea}", idea, {"idea": idea}, claude, wait)
        return idea

    def start_screen(self, *, text: str = "", url: str = "", image: bytes | None = None, wait: bool = False) -> Path:
        """Saves what Ahmad pasted / uploaded, then runs /add-from-screen on it."""
        claude = self._check_can_start()
        path = screen.save_input(text=text, url=url, image=image, source="dashboard", paste_dir=self.paste_dir)
        rel = path.relative_to(db.ROOT).as_posix() if path.is_relative_to(db.ROOT) else path.as_posix()
        self.start_job("screen", f"/add-from-screen {rel}", f"add from screen: {path.name}",
                       {"file": path.name}, claude, wait)
        return path

    # ---------- page ----------
    def page(self) -> str:
        store = self._open_store()
        try:
            desk = db.Desk(store, **self.desk_kwargs)
            return render(self, desk)
        finally:
            store.close()


# ---------- html parts ----------
def _section(title: str, body: str, sid: str) -> str:
    return f"<section id='{sid}'><h2>{esc(title)}</h2>{body}</section>"


def _today_html(desk: db.Desk) -> str:
    sections = today_mod.report(desk)
    if not sections:
        return "<p class='empty'>Nothing needs you today.</p>"
    out = []
    for title, lines in sections:
        items = "".join(f"<li>{esc(x)}</li>" for x in lines)
        out.append(f"<div class='box'><h3>{esc(title)} <span class='n'>{len(lines)}</span></h3><ul>{items}</ul></div>")
    return "".join(out)


def _pipeline_html(desk: db.Desk) -> str:
    counts = digest.pipeline(desk)
    if not counts:
        return "<p class='empty'>No leads yet.</p>"
    cells = "".join(f"<div class='stat{' end' if s in db.END_STATES else ''}'><b>{n}</b><span>{esc(s)}</span></div>"
                    for s, n in counts.items())
    return f"<div class='stats'>{cells}</div>"


def _numbers_html(desk: db.Desk) -> str:
    week, total = digest.numbers(desk, 7), digest.numbers(desk)
    rows = "".join(f"<tr><td>{esc(label)}</td><td>{week[k]}</td><td>{total[k]}</td></tr>"
                   for k, label in digest.NUMBER_NAMES)
    return (f"<table><thead><tr><th></th><th>last 7 days</th><th>all time</th></tr></thead>"
            f"<tbody>{rows}</tbody></table>")


def _cards_html(desk: db.Desk) -> str:
    found = cards_mod.collect(desk)
    if not found:
        return "<p class='empty'>No qualified leads yet. Run /research on new leads, then /cards.</p>"
    return "".join(cards_mod.card_html(c, i) for i, c in enumerate(found, 1))


def _ideas_html(app: App, desk: db.Desk) -> str:
    leads = desk.store.list_leads()
    found = scout.stats(desk)["by_idea"]
    rows = []
    for idea in ideas_mod.list_ideas(desk.config_dir):
        name = idea["name"]
        mine = [l for l in leads if l.get("idea") == name]
        good = sum(1 for l in mine if l["status"] in GOOD_STATES)
        rows.append(f"<tr><td><b>{esc(name)}</b><br><small>{esc(idea.get('idea'))}</small></td>"
                    f"<td>{esc(str(idea.get('created') or '')[:10])}</td>"
                    f"<td>{(found.get(name) or {}).get('found', 0)}</td><td>{len(mine)}</td><td>{good}</td></tr>")
    table = ("<table><thead><tr><th>idea</th><th>made</th><th>found</th><th>leads</th><th>good leads</th></tr>"
             f"</thead><tbody>{''.join(rows)}</tbody></table>" if rows else "<p class='empty'>No idea searches yet.</p>")
    recent = ""
    if not app.demo:
        runs = [r for r in daily.read_runs(app.logs) if r.get("kind") == "search"][-5:]
        if runs:
            items = "".join(
                f"<li>{esc(db.to_local(r.get('started'), desk.tz))} · \"{esc(r.get('idea'))}\" · "
                f"{'OK' if r.get('ok') else 'FAILED'} · {esc(r.get('minutes'))} min · log: logs/{esc(r.get('log'))}</li>"
                for r in reversed(runs))
            recent = f"<h3>Last searches from this page</h3><ul>{items}</ul>"
    return table + recent


def _search_html(app: App) -> str:
    if app.demo:
        return "<p class='muted'>Search is off in demo mode.</p>"
    running = app.searching()
    if running:
        return _busy_html(running)
    return (f"<form method='post' action='/search'><input type='hidden' name='token' value='{esc(app.token)}'>"
            "<input name='idea' maxlength='200' required placeholder='dental clinics that need booking automation'>"
            "<button type='submit'>Search an idea</button></form>"
            "<p class='muted small'>Runs /search-idea in the background (max 10 leads). Each search uses part of "
            "your Claude subscription limit. It never sends anything.</p>")


def _busy_html(running: dict) -> str:
    return (f"<p class='busy'>Working… \"{esc(running.get('what'))}\" (started "
            f"{esc(db.to_local(running.get('started'), db.local_tz()))}). This page refreshes by itself. "
            f"New leads appear in the Today list, under Ideas, and in the runs table.</p>")


def _screen_html(app: App) -> str:
    if app.demo:
        return "<p class='muted'>Off in demo mode.</p>"
    running = app.searching()
    if running:
        return _busy_html(running)
    return (f"<form method='post' action='/add-screen' enctype='multipart/form-data' class='stack'>"
            f"<input type='hidden' name='token' value='{esc(app.token)}'>"
            "<textarea name='text' rows='5' maxlength='20000' placeholder='Paste the text of a LinkedIn post or "
            "profile, an Upwork job, a forum post...'></textarea>"
            "<input name='url' type='url' maxlength='500' placeholder='Link of that page (if you have it)'>"
            "<label class='small'>or a screenshot (PNG/JPG, max 5 MB): "
            "<input type='file' name='image' accept='image/png,image/jpeg'></label>"
            "<button type='submit'>Add from screen</button></form>"
            "<p class='muted small'>Claude reads it, saves the exact words as proof, makes the lead and researches the "
            "company website (never LinkedIn). Uses part of your Claude subscription limit. Nothing is sent.</p>")


def _send_by_hand_html(desk: db.Desk) -> str:
    """Approved messages for channels that are not email: Ahmad copies, pastes and sends them himself."""
    leads = {l["id"]: l for l in desk.store.list_leads()}
    latest: dict[int, dict] = {}
    for m in desk.store.list_messages():
        if m.get("direction") == "out":
            latest[m["opportunity_id"]] = m
    items = []
    for lid, m in latest.items():
        lead = leads.get(lid)
        if not lead or lead["status"] in db.END_STATES or m.get("sent_at") or (m.get("channel") or "") == "email":
            continue
        if desk.draft_state(m) != "approved, copy-paste file written":
            continue
        text = (f"Subject: {m['subject']}\n\n" if m.get("subject") else "") + (m.get("body") or "")
        touch = int(m.get("touch_number") or 1)
        follow = "" if touch == 1 else f" · follow-up {touch}"
        items.append(
            f"<div class='box'><h3>#{lid} {esc(lead.get('company_name'))} · {esc(m.get('channel'))}{follow}</h3>"
            f"<textarea id='msg-{m['id']}' rows='5' readonly>{esc(text)}</textarea>"
            f"<p><button type='button' class='copy' data-target='msg-{m['id']}'>Copy</button> "
            f"<span class='muted small'>Paste it in {esc(m.get('channel'))} and press Send yourself. Then run: "
            f"<code>python scripts/desk.py mark-sent M{m['id']}</code></span></p></div>")
    return "".join(items) or "<p class='empty'>Nothing to send by hand.</p>"


# The only script on the page: the Copy buttons. Allowed by a new random nonce for every page (CSP).
COPY_JS = """document.querySelectorAll('button.copy').forEach(function(b){b.addEventListener('click',function(){
var t=document.getElementById(b.dataset.target);var done=function(){b.textContent='Copied';
setTimeout(function(){b.textContent='Copy';},1500);};
var old=function(){t.select();document.execCommand('copy');done();};
if(navigator.clipboard){navigator.clipboard.writeText(t.value).then(done,old);}else{old();}});});"""


def _runs_html(app: App, desk: db.Desk) -> str:
    if app.demo:
        return "<p class='muted'>Not shown in demo mode.</p>"
    runs = daily.read_runs(app.logs)[-10:]
    if not runs:
        return "<p class='empty'>No runs yet. Try: python scripts/daily.py run</p>"
    rows = "".join(
        f"<tr><td>{esc(db.to_local(r.get('started'), desk.tz))}</td><td>{esc(r.get('kind'))}</td>"
        f"<td class='{'ok' if r.get('ok') else 'bad'}'>{'OK' if r.get('ok') else 'FAILED'}</td>"
        f"<td>{esc(r.get('minutes'))}</td><td>{esc(r.get('turns') or '')}</td>"
        f"<td>{'' if r.get('cost_usd') is None else '$' + format(float(r['cost_usd']), '.2f')}</td>"
        f"<td>logs/{esc(r.get('log'))}</td></tr>" for r in reversed(runs))
    return ("<table><thead><tr><th>start</th><th>kind</th><th></th><th>min</th><th>steps</th><th>API-equal cost</th>"
            f"<th>log</th></tr></thead><tbody>{rows}</tbody></table>"
            "<p class='muted small'>Time and cost notes: you pay the Claude subscription, not this dollar number. "
            "It only shows how heavy a run was. Big numbers use more of your limit.</p>")


def _todo_html(desk: db.Desk) -> str:
    report = config_check.run_checks(desk.config_dir or db.ROOT / "config")
    items = report.errors + report.warnings
    if not items:
        return "<p class='muted'>Config is complete.</p>"
    lis = "".join(f"<li>{esc(x)}</li>" for x in items)
    return (f"<ul>{lis}</ul><p class='muted small'>Fill these in the config files when you decide. "
            "Email drafts go to Gmail only when this list is empty (config_check --strict).</p>")


CSS = cards_mod.CSS + """
main{max-width:980px}nav{display:flex;flex-wrap:wrap;gap:6px 14px;margin:8px 0 18px;font-size:14px}
section{margin:0 0 28px}section>h2{font-size:18px;margin:0 0 10px;border-bottom:1px solid var(--line);padding-bottom:6px}
.box{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 14px;margin:0 0 10px}
.box h3{margin:2px 0 6px}.box li{margin:3px 0;overflow-wrap:anywhere}.n{background:var(--accent);color:var(--card);
border-radius:10px;padding:0 7px;font-size:12px}
.stats{display:flex;flex-wrap:wrap;gap:8px}.stat{background:var(--card);border:1px solid var(--line);border-radius:8px;
padding:8px 12px;min-width:92px}.stat b{display:block;font-size:22px}.stat span{font-size:12px;color:var(--muted)}
.stat.end{opacity:.75}
table{border-collapse:collapse;width:100%;background:var(--card);border:1px solid var(--line);border-radius:8px;
font-size:14px;display:block;overflow-x:auto}th,td{text-align:left;padding:6px 10px;border-bottom:1px solid var(--line);
vertical-align:top}td.ok{color:var(--strong)}td.bad{color:var(--warn)}
form{display:flex;gap:8px;flex-wrap:wrap}form input[name=idea]{flex:1;min-width:220px;padding:9px 10px;
border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--text);font:inherit}
form.stack{flex-direction:column;align-items:stretch}form.stack input[name=url],textarea{width:100%;padding:9px 10px;
border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--text);font:inherit}
textarea{resize:vertical}form.stack button{align-self:flex-start}code{font-size:12px;overflow-wrap:anywhere}
button{padding:9px 14px;border:0;border-radius:8px;background:var(--accent);color:var(--card);font:inherit;
font-weight:600;cursor:pointer}.small{font-size:13px}.busy,.flash{background:var(--warn-bg);color:var(--warn);
padding:8px 12px;border-radius:8px}.banner{background:var(--accent);color:var(--card);padding:10px 14px;
border-radius:8px;font-weight:700;margin:0 0 12px}.stop{background:var(--warn-bg);color:var(--warn);padding:10px 14px;
border-radius:8px;font-weight:700;margin:0 0 12px}
"""


def render(app: App, desk: db.Desk) -> str:
    now = desk.now().astimezone(desk.tz)
    nonce = secrets.token_urlsafe(16)                 # new for every page; the CSP header repeats it
    refresh = "<meta http-equiv='refresh' content='15'>" if (not app.demo and app.searching()) else ""
    banner = ("<div class='banner'>DEMO - made-up businesses only. No real names, no real emails.</div>"
              if app.demo else "")
    stop = ("<div class='stop'>STOP is ON: data/STOP exists. The morning run and searches do nothing. "
            "Delete the file to start again.</div>" if app.stop_file.exists() and not app.demo else "")
    flash = f"<p class='flash'>{esc(app.message)}</p>" if app.message else ""
    app.message = ""
    parts = [
        _section("Search an idea", _search_html(app), "search"),
        _section("Add from screen", _screen_html(app), "screen"),
        _section("Today", _today_html(desk), "today"),
        _section("Send by hand (LinkedIn, Upwork, agency, referral)", _send_by_hand_html(desk), "send"),
        _section("Pipeline", _pipeline_html(desk), "pipeline"),
        _section("Numbers", _numbers_html(desk), "numbers"),
        _section("Cards (best first)", _cards_html(desk), "cards"),
        _section("Ideas searched", _ideas_html(app, desk), "ideas"),
        _section("Morning runs and searches", _runs_html(app, desk), "runs"),
        _section("Things to decide later", _todo_html(desk), "todo"),
    ]
    nav = "".join(f"<a href='#{sid}'>{label}</a>" for sid, label in
                  (("today", "Today"), ("send", "Send by hand"), ("screen", "Add from screen"), ("pipeline", "Pipeline"), ("numbers", "Numbers"), ("cards", "Cards"),
                   ("ideas", "Ideas"), ("runs", "Runs"), ("todo", "To decide")))
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">{refresh}
<title>Opportunity Desk</title>
<style>{CSS}</style></head>
<body><main>
{banner}{stop}
<h1>Opportunity Desk{' (demo)' if app.demo else ''}</h1>
<p class="muted">{now:%A %d %B %Y, %H:%M} (Asia/Karachi) · only on this PC · nothing is ever sent from here</p>
<nav>{nav}</nav>
{flash}
{''.join(parts)}
</main><script nonce="{nonce}">{COPY_JS}</script></body></html>
"""


# ---------- web server ----------
def make_handler(app: App):
    allowed_hosts = {f"{HOST}:{app.port}", f"localhost:{app.port}"}

    class Handler(BaseHTTPRequestHandler):
        server_version = "OpportunityDesk"

        def log_message(self, fmt, *args):          # quiet console
            pass

        def _send(self, code: int, body: str, ctype: str = "text/html; charset=utf-8", extra: dict | None = None):
            data = body.encode("utf-8")
            nonce = NONCE_RE.search(body) if ctype.startswith("text/html") else None
            script = f" script-src 'nonce-{nonce.group(1)}';" if nonce else ""
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy",
                             "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none';"
                             + script)
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def _host_ok(self) -> bool:
            return (self.headers.get("Host") or "") in allowed_hosts

        def do_GET(self):
            if not self._host_ok():
                return self._send(403, "Forbidden host", "text/plain; charset=utf-8")
            path = self.path.split("?")[0]
            if path == "/":
                try:
                    return self._send(200, app.page())
                except db.DeskError as exc:
                    return self._send(500, f"<p>ERROR: {esc(exc)}</p>")
            if path == "/status":
                running = app.searching()
                return self._send(200, json.dumps({"searching": bool(running)}), "application/json")
            return self._send(404, "Not found", "text/plain; charset=utf-8")

        def do_POST(self):
            if not self._host_ok():
                return self._send(403, "Forbidden host", "text/plain; charset=utf-8")
            if self.path == "/add-screen":
                return self._add_screen()
            if self.path != "/search":
                return self._send(404, "Not found", "text/plain; charset=utf-8")
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                return self._send(413, "Too big", "text/plain; charset=utf-8")
            form = parse_qs(self.rfile.read(length).decode("utf-8", errors="replace"))
            if not secrets.compare_digest((form.get("token") or [""])[0], app.token):
                return self._send(403, "Bad token. Reload the page.", "text/plain; charset=utf-8")
            try:
                idea = app.start_search((form.get("idea") or [""])[0])
                app.message = f"Search started: \"{idea}\". This page refreshes by itself."
            except (db.DeskError, daily.RunError) as exc:
                app.message = f"Search not started: {exc}"
            return self._send(303, "", extra={"Location": "/"})

        def _add_screen(self):
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_UPLOAD:
                return self._send(413, "Too big (screenshot max 5 MB)", "text/plain; charset=utf-8")
            form = parse_multipart(self.headers.get("Content-Type") or "", self.rfile.read(length))
            token = form.get("token")
            if not isinstance(token, str) or not secrets.compare_digest(token, app.token):
                return self._send(403, "Bad token. Reload the page.", "text/plain; charset=utf-8")
            image = form.get("image")
            text, url = form.get("text"), form.get("url")
            try:
                path = app.start_screen(text=text if isinstance(text, str) else "",
                                        url=url if isinstance(url, str) else "",
                                        image=image if isinstance(image, bytes) and image else None)
                app.message = f"Saved {path.name}. Claude is making the lead now. This page refreshes by itself."
            except (db.DeskError, daily.RunError) as exc:
                app.message = f"Not added: {exc}"
            return self._send(303, "", extra={"Location": "/"})

    return Handler


def parse_multipart(content_type: str, body: bytes) -> dict:
    """multipart/form-data -> {name: str (text field) | bytes (file)}. Anything else -> {}."""
    if not content_type.lower().startswith("multipart/form-data"):
        return {}
    head = f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("latin-1", errors="replace")
    msg = BytesParser(policy=HTTP).parsebytes(head + body)
    if not msg.is_multipart():
        return {}
    out: dict = {}
    for part in msg.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if not name:
            continue
        data = part.get_payload(decode=True) or b""
        out[name] = data if part.get_filename() is not None else data.decode("utf-8", errors="replace")
    return out


def serve(app: App) -> None:
    server = ThreadingHTTPServer((HOST, app.port), make_handler(app))
    print(f"Dashboard{' (DEMO)' if app.demo else ''}: http://{HOST}:{app.port}   (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Opportunity Desk dashboard (127.0.0.1 only).")
    ap.add_argument("--demo", action="store_true", help="made-up businesses only (data/demo.db)")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = ap.parse_args(argv)
    try:
        app = App(demo=args.demo, port=args.port)
        app.page()                                    # fail early with a clear message (no .env, no demo data)
    except db.DeskError as exc:
        print(f"ERROR: {exc}")
        return 1
    serve(app)
    return 0


if __name__ == "__main__":
    sys.exit(main())
