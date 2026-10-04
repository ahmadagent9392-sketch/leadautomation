"""Tests for the Stage 8 dashboard (scripts/dashboard.py): page, safety checks, idea search.
SQLite + a fake `claude` runner; the web server runs on 127.0.0.1 with a free port."""
import http.client
import re
import subprocess
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlencode

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import daily  # noqa: E402
import dashboard  # noqa: E402
import db  # noqa: E402
import demo  # noqa: E402
from store_sqlite import SqliteStore  # noqa: E402

EVIL = "Evil <script>alert(1)</script> Co"


class FakeRunner:
    def __init__(self):
        self.calls = []

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, '{"result": "found 2 leads", "is_error": false}', "")


@pytest.fixture(scope="module")
def demo_db(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("dash")
    store = SqliteStore(tmp / "demo.db")
    demo.seed(store, tmp / "demo")
    store.close()
    return tmp


def make_app(tmp_path, demo_db=None, *, demo_mode=False, runner=None, db_file=None):
    path = db_file or (demo_db / "demo.db")
    kwargs = ({"config_dir": demo_db / "demo" / "config", "snapshot_dir": demo_db / "demo" / "snapshots"}
              if demo_db else {"snapshot_dir": tmp_path / "s"})
    return dashboard.App(demo=demo_mode, logs=tmp_path / "logs", stop_file=tmp_path / "STOP",
                         open_store=lambda: SqliteStore(path), desk_kwargs=kwargs,
                         runner=runner or FakeRunner(), claude="claude")


@pytest.fixture
def real_app(tmp_path, demo_db):
    """Not demo mode (search on), but the made-up data, so nothing real is touched."""
    return make_app(tmp_path, demo_db)


# ---------- page ----------
def test_page_has_all_sections(real_app):
    html = real_app.page()
    for part in ("<title>Opportunity Desk</title>", "Search an idea", "id='today'", "Drafts waiting for your OK",
                 "id='pipeline'", "id='numbers'", "last 7 days", "Cards (best first)", "Greenleaf Accounting (demo)",
                 "demo-trades-booking", "Morning runs", "No runs yet", "subscription limit", f"value='{real_app.token}'"):
        assert part in html, part
    assert "DEMO - made-up" not in html


def test_demo_page_banner_no_search_no_logs(tmp_path, demo_db):
    app = make_app(tmp_path, demo_db, demo_mode=True)
    daily.add_run({"kind": "search", "idea": "REAL IDEA TEXT", "ok": True, "started": "2026-10-04T02:00:00+00:00"},
                  tmp_path / "logs")
    html = app.page()
    assert "DEMO - made-up businesses only" in html
    assert "<form" not in html and "Search is off in demo mode" in html
    assert "REAL IDEA TEXT" not in html                                 # real logs never shown in demo
    with pytest.raises(db.DeskError, match="demo"):
        app.start_search("dental clinics")


def test_page_escapes_text(tmp_path, demo_db):
    """Text from the database, logs and config is shown, never run as HTML."""
    store = SqliteStore(tmp_path / "e.db")
    desk = db.Desk(store, snapshot_dir=tmp_path / "s")
    lead = desk.add_lead("https://evil-co.com/jobs", "x", "email", company=EVIL)
    store.conn.execute("UPDATE opportunities SET updated_at = '2026-01-01T00:00:00+00:00', "
                       "created_at = '2026-01-01T00:00:00+00:00' WHERE id = ?", (lead,))   # stale -> on Today
    store.conn.commit()
    store.close()
    app = make_app(tmp_path, db_file=tmp_path / "e.db")
    daily.take_lock(tmp_path / "logs", daily.SEARCH_LOCK_NAME, what=EVIL)
    html = app.page()
    assert "<script>" not in html and html.count("&lt;script&gt;") >= 2


def test_stop_banner(real_app, tmp_path):
    (tmp_path / "STOP").write_text("")
    assert "STOP is ON" in real_app.page()


def test_runs_table(real_app, tmp_path):
    daily.add_run({"kind": "daily", "ok": True, "started": "2026-10-04T02:30:00+00:00", "minutes": 12.5,
                   "turns": 80, "cost_usd": 3.456, "log": "daily-x.log"}, tmp_path / "logs")
    html = real_app.page()
    assert "2026-10-04 07:30" in html and "$3.46" in html and "logs/daily-x.log" in html


# ---------- idea text ----------
@pytest.mark.parametrize("bad", ["", "ab", "dental & rm -rf", 'say "hi"', "a|b", "x" * 201, "100%", "<b>",
                                 "wow!", "a^b"])
def test_bad_idea_text_refused(bad):
    with pytest.raises(db.DeskError):
        dashboard.check_idea(bad)


def test_idea_text_made_one_line():
    assert dashboard.check_idea("  dental clinics\n that need   booking ") == "dental clinics that need booking"


# ---------- search ----------
def test_search_runs_claude_with_the_fixed_tools(real_app, tmp_path):
    idea = real_app.start_search("dental clinics that need booking automation", wait=True)
    cmd = real_app.runner.calls[0]
    assert cmd[:3] == ["claude", "-p", f"/search-idea {idea}"]
    assert cmd[cmd.index("--permission-mode") + 1] == "dontAsk"
    row = daily.read_runs(tmp_path / "logs")[-1]
    assert row["kind"] == "search" and row["idea"] == idea and row["ok"]
    assert real_app.searching() is None                                # lock freed
    assert "dental clinics that need booking automation" in real_app.page()   # in "Last searches"


def test_search_refused_while_busy_or_stopped(real_app, tmp_path):
    daily.take_lock(tmp_path / "logs", daily.LOCK_NAME)
    with pytest.raises(db.DeskError, match="morning run"):
        real_app.start_search("dental clinics")
    daily.free_lock(tmp_path / "logs", daily.LOCK_NAME)
    daily.take_lock(tmp_path / "logs", daily.SEARCH_LOCK_NAME, what="other idea")
    with pytest.raises(db.DeskError, match="another background job"):
        real_app.start_search("dental clinics")
    assert "Working" in real_app.page() and "http-equiv='refresh'" in real_app.page()
    daily.free_lock(tmp_path / "logs", daily.SEARCH_LOCK_NAME)
    (tmp_path / "STOP").write_text("")
    with pytest.raises(db.DeskError, match="STOP"):
        real_app.start_search("dental clinics")
    assert real_app.runner.calls == []


# ---------- the web server ----------
@pytest.fixture
def server(real_app):
    srv = ThreadingHTTPServer((dashboard.HOST, 0), None)
    real_app.port = srv.server_address[1]
    srv.RequestHandlerClass = dashboard.make_handler(real_app)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield real_app
    srv.shutdown()
    srv.server_close()


def request(app, method, path, body=None, host=None):
    conn = http.client.HTTPConnection(dashboard.HOST, app.port, timeout=10)
    headers = {"Host": host or f"127.0.0.1:{app.port}"}
    if body is not None:
        body = urlencode(body)
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    conn.request(method, path, body=body, headers=headers)
    resp = conn.getresponse()
    data = resp.read().decode("utf-8")
    conn.close()
    return resp, data


def test_listens_on_localhost_only():
    assert dashboard.HOST == "127.0.0.1"


def test_get_page_and_headers(server):
    resp, html = request(server, "GET", "/")
    assert resp.status == 200 and "Opportunity Desk" in html
    assert "default-src 'none'" in resp.getheader("Content-Security-Policy")
    assert resp.getheader("X-Frame-Options") == "DENY"
    assert request(server, "GET", "/nothing")[0].status == 404


def test_wrong_host_refused(server):
    assert request(server, "GET", "/", host="evil.example")[0].status == 403
    resp, _ = request(server, "POST", "/search", {"token": server.token, "idea": "dental clinics"},
                      host="evil.example:80")
    assert resp.status == 403 and server.runner.calls == []


def test_post_needs_the_token(server):
    resp, _ = request(server, "POST", "/search", {"token": "guess", "idea": "dental clinics"})
    assert resp.status == 403 and server.runner.calls == []


def test_post_search_starts_and_redirects(server):
    resp, _ = request(server, "POST", "/search", {"token": server.token, "idea": "plumbers missing calls"})
    assert resp.status == 303 and resp.getheader("Location") == "/"
    server.thread.join(10)
    assert server.runner.calls[0][2] == "/search-idea plumbers missing calls"
    _, html = request(server, "GET", "/")
    assert "Search started" in html


def test_post_bad_idea_shows_message(server):
    resp, _ = request(server, "POST", "/search", {"token": server.token, "idea": "x & y"})
    assert resp.status == 303 and server.runner.calls == []
    _, html = request(server, "GET", "/")
    assert "Search not started" in html


def test_only_script_is_the_copy_button(real_app):
    html = real_app.page()
    assert len(re.findall(r"<script", html, re.I)) == 1
    assert dashboard.NONCE_RE.search(html) and "navigator.clipboard" in html
