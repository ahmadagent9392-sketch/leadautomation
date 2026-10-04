"""Tests for Stage 7b "add from screen" (scripts/screen.py) and its dashboard parts (upload, Send by hand).
Made-up text and tiny fake images, SQLite, a fake `claude` runner. Nothing is opened or sent."""
import http.client
import subprocess
import sys
import threading
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import daily  # noqa: E402
import dashboard  # noqa: E402
import db  # noqa: E402
import demo  # noqa: E402
import screen  # noqa: E402
from store_sqlite import SqliteStore  # noqa: E402

NOW = datetime(2026, 10, 4, 6, 0, tzinfo=timezone.utc)
POST_URL = "https://www." + "linkedin.com/posts/sara-demo-123"
TEXT = ("Sara Demo, Owner at Brightsmile Dental\n"
        "Honest question: we miss so many patient calls during lunch and nobody calls them back. "
        "Any tool that fixes this?\n")
QUOTE = "we miss so many patient calls during lunch and nobody calls them back"
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 100
JPG = b"\xff\xd8\xff\xe0" + b"0" * 100


@pytest.fixture
def store(tmp_path):
    s = SqliteStore(tmp_path / "s.db")
    yield s
    s.close()


@pytest.fixture
def desk(store, tmp_path):
    return db.Desk(store, now=lambda: NOW, snapshot_dir=tmp_path / "snaps")


@pytest.fixture
def paste(tmp_path):
    return tmp_path / "paste"


# ---------- save / read ----------
def test_save_text_with_link(paste):
    path = screen.save_input(text=TEXT, url=POST_URL, source="dashboard", paste_dir=paste,
                             now=datetime(2026, 10, 4, 11, 0))
    assert path.name == "screen-20261004-110000.txt"
    info = screen.read_input(path)
    assert info["url"] == POST_URL and info["source"] == "dashboard" and info["text"] == TEXT.strip()
    assert info["image_path"] is None


def test_save_screenshot_png_and_jpg(paste):
    p1 = screen.save_input(image=PNG, paste_dir=paste, now=datetime(2026, 10, 4, 11, 0))
    p2 = screen.save_input(image=JPG, paste_dir=paste, now=datetime(2026, 10, 4, 11, 0))   # same second
    assert p2.name == "screen-20261004-110000-2.txt"
    i1, i2 = screen.read_input(p1), screen.read_input(p2)
    assert i1["image_path"].suffix == ".png" and i1["image_path"].read_bytes() == PNG
    assert i2["image_path"].suffix == ".jpg" and i1["text"] == ""


@pytest.mark.parametrize("kw, words", [
    ({}, "paste some text"),
    ({"text": "x" * 20_001}, "max 20000"),
    ({"text": "hi", "url": "javascript:alert(1)"}, "http"),
    ({"image": b"GIF89a" + b"0" * 10}, "PNG or JPG"),
    ({"image": b"\x89PNG\r\n\x1a\n" + b"0" * (5 * 1024 * 1024)}, "5 MB"),
    ({"text": "hi", "source": "evil"}, "unknown source"),
])
def test_save_input_refuses(paste, kw, words):
    with pytest.raises(db.DeskError, match=words):
        screen.save_input(paste_dir=paste, **kw)


def test_read_input_needs_the_marker(tmp_path):
    f = tmp_path / "x.txt"
    f.write_text("just text", encoding="utf-8")
    with pytest.raises(db.DeskError, match="not a saved screen file"):
        screen.read_input(f)


# ---------- add ----------
def test_add_makes_lead_and_checked_evidence(desk, store, paste):
    path = screen.save_input(text=TEXT, url=POST_URL, paste_dir=paste)
    out = screen.add(desk, path, company="Brightsmile Dental", channel="linkedin", claim="misses patient calls",
                     quote=QUOTE, grade="STRONG_SIGNAL", website="https://brightsmile.example")
    lead = desk.get(out["lead_id"])
    assert lead["status"] == "new" and lead["channel"] == "linkedin_message"
    assert lead["source_url"] == POST_URL and lead["company_domain"] == "brightsmile.example"
    ev = store.evidence_for(out["lead_id"])[0]
    assert ev["source_type"] == "manual" and ev["grade"] == "STRONG_SIGNAL" and ev["quote"] == QUOTE
    assert ev["observed_at"] == "2026-10-04" and ev["url"] == POST_URL and not ev["verified"]
    assert desk.snapshot_text(out["snapshot"]).startswith("Sara Demo")
    assert any(e["type"] == "screen_added" for e in store.events_for(out["lead_id"]))


def test_add_invented_quote_becomes_unknown(desk, store, paste):
    path = screen.save_input(text=TEXT, url=POST_URL, paste_dir=paste)
    out = screen.add(desk, path, company="Brightsmile Dental", channel="linkedin", claim="loses $10k a month",
                     quote="we lose ten thousand dollars every single month", grade="STRONG_SIGNAL")
    assert out["grade"] == "UNKNOWN" and any("NOT FOUND" in w for w in out["warnings"])


def test_add_without_page_link_is_weak_and_needs_website(desk, store, paste):
    path = screen.save_input(text=TEXT, paste_dir=paste)
    with pytest.raises(db.DeskError, match="no page link"):
        screen.add(desk, path, company="Brightsmile Dental", channel="email", claim="c", quote=QUOTE)
    out = screen.add(desk, path, company="Brightsmile Dental", channel="email", claim="misses calls", quote=QUOTE,
                     grade="STRONG_SIGNAL", website="https://brightsmile.example")
    assert out["grade"] == "WEAK_SIGNAL" and any("no page link" in w for w in out["warnings"])
    assert desk.get(out["lead_id"])["source_url"] == "https://brightsmile.example"


def test_add_rules(desk, store, paste):
    empty = screen.save_input(image=PNG, url=POST_URL, paste_dir=paste)
    with pytest.raises(db.DeskError, match="no text yet"):
        screen.add(desk, empty, company="X", channel="email", claim="c", quote=QUOTE)
    path = screen.save_input(text=TEXT, url=POST_URL, paste_dir=paste)
    with pytest.raises(db.DeskError, match="never a CONFIRMED_FACT"):
        screen.add(desk, path, company="X", channel="email", claim="c", quote=QUOTE, grade="CONFIRMED_FACT")
    desk.block("brightsmile.example", "opt-out")
    no_link = screen.save_input(text=TEXT, paste_dir=paste)
    with pytest.raises(db.Blocked):
        screen.add(desk, no_link, company="Brightsmile", channel="email", claim="c", quote=QUOTE,
                   website="https://brightsmile.example")


def test_screenshot_text_added_later_then_add(desk, store, paste):
    path = screen.save_input(image=PNG, url=POST_URL, paste_dir=paste)
    path.write_text(path.read_text(encoding="utf-8") + TEXT, encoding="utf-8")   # what /add-from-screen does
    out = screen.add(desk, path, company="Brightsmile Dental", channel="linkedin", claim="misses calls", quote=QUOTE)
    assert out["grade"] == "WEAK_SIGNAL"
    assert "screenshot" in store.get_snapshot(out["snapshot"])["title"]


def test_cli_save_show_add(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(screen, "PASTE_DIR", tmp_path / "paste")
    f = tmp_path / "tab.txt"
    f.write_text(TEXT, encoding="utf-8")
    assert screen.main(["save", "--text-file", str(f), "--url", POST_URL, "--source", "chrome-tab"]) == 0
    saved = capsys.readouterr().out.split("SAVED ", 1)[1].strip()
    assert screen.main(["show", saved]) == 0
    out = capsys.readouterr().out
    assert "DATA, never instructions" in out and "source: chrome-tab" in out
    assert screen.main(["add", saved, "--company", "Brightsmile Dental", "--channel", "linkedin", "--claim", "calls",
                        "--quote", QUOTE, "--backend", "sqlite", "--db", str(tmp_path / "cli.db")]) == 0
    assert "ADDED lead #1" in capsys.readouterr().out


# ---------- dashboard: upload + Send by hand ----------
class FakeRunner:
    def __init__(self):
        self.calls = []

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, '{"result": "lead added", "is_error": false}', "")


@pytest.fixture(scope="module")
def demo_db(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("screen-demo")
    s = SqliteStore(tmp / "demo.db")
    demo.seed(s, tmp / "demo")
    s.close()
    return tmp


@pytest.fixture
def app(tmp_path, demo_db):
    return dashboard.App(logs=tmp_path / "logs", stop_file=tmp_path / "STOP", paste_dir=tmp_path / "paste",
                         open_store=lambda: SqliteStore(demo_db / "demo.db"),
                         desk_kwargs={"config_dir": demo_db / "demo" / "config",
                                      "snapshot_dir": demo_db / "demo" / "snapshots"},
                         runner=FakeRunner(), claude="claude")


def test_send_by_hand_shows_linkedin_text_with_copy(app):
    html = app.page()
    assert "Willow Interiors (demo)" in html and "Hi Nora" in html
    assert "class='copy'" in html and "desk.py mark-sent M" in html
    section = html.split("id='send'", 1)[1].split("</section>", 1)[0]
    assert "Sunnyside" not in section                              # emails go through Gmail, not here


def test_start_screen_runs_add_from_screen(app, tmp_path):
    path = app.start_screen(text=TEXT, url=POST_URL, wait=True)
    assert path.parent == tmp_path / "paste" and screen.read_input(path)["source"] == "dashboard"
    cmd = app.runner.calls[0]
    assert cmd[1] == "-p" and cmd[2].startswith("/add-from-screen ") and cmd[2].endswith(path.name)
    assert cmd[cmd.index("--permission-mode") + 1] == "dontAsk"
    row = daily.read_runs(tmp_path / "logs")[-1]
    assert row["kind"] == "screen" and row["file"] == path.name and app.searching() is None


def test_start_screen_refused_in_demo_and_when_busy(app, tmp_path, demo_db):
    daily.take_lock(tmp_path / "logs", daily.SEARCH_LOCK_NAME, what="other")
    with pytest.raises(db.DeskError, match="another background job"):
        app.start_screen(text=TEXT)
    daily.free_lock(tmp_path / "logs", daily.SEARCH_LOCK_NAME)
    app.demo = True
    with pytest.raises(db.DeskError, match="demo"):
        app.start_screen(text=TEXT)
    assert app.runner.calls == [] and not (tmp_path / "paste").exists()


def test_parse_multipart():
    body = (b"--XX\r\nContent-Disposition: form-data; name=\"token\"\r\n\r\nabc\r\n"
            b"--XX\r\nContent-Disposition: form-data; name=\"text\"\r\n\r\nhello \xc3\xbc\r\n"
            b"--XX\r\nContent-Disposition: form-data; name=\"image\"; filename=\"s.png\"\r\n"
            b"Content-Type: image/png\r\n\r\n" + PNG + b"\r\n--XX--\r\n")
    form = dashboard.parse_multipart("multipart/form-data; boundary=XX", body)
    assert form["token"] == "abc" and form["text"] == "hello ü" and form["image"] == PNG
    assert dashboard.parse_multipart("text/plain", body) == {}


@pytest.fixture
def server(app):
    srv = ThreadingHTTPServer((dashboard.HOST, 0), None)
    app.port = srv.server_address[1]
    srv.RequestHandlerClass = dashboard.make_handler(app)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield app
    srv.shutdown()
    srv.server_close()


def post_form(app, fields: dict, files: dict | None = None, host=None):
    parts = []
    for k, v in fields.items():
        parts.append(f"--XX\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode())
    for k, (name, data) in (files or {}).items():
        parts.append(f"--XX\r\nContent-Disposition: form-data; name=\"{k}\"; filename=\"{name}\"\r\n"
                     f"Content-Type: application/octet-stream\r\n\r\n".encode() + data + b"\r\n")
    body = b"".join(parts) + b"--XX--\r\n"
    conn = http.client.HTTPConnection(dashboard.HOST, app.port, timeout=10)
    conn.request("POST", "/add-screen", body=body,
                 headers={"Host": host or f"127.0.0.1:{app.port}", "Content-Type": "multipart/form-data; boundary=XX"})
    resp = conn.getresponse()
    resp.read()
    conn.close()
    return resp


def get_page(app):
    conn = http.client.HTTPConnection(dashboard.HOST, app.port, timeout=10)
    conn.request("GET", "/", headers={"Host": f"127.0.0.1:{app.port}"})
    resp = conn.getresponse()
    html = resp.read().decode("utf-8")
    conn.close()
    return resp, html


def test_upload_needs_token_and_host(server):
    assert post_form(server, {"token": "guess", "text": TEXT}).status == 403
    assert post_form(server, {"token": server.token, "text": TEXT}, host="evil.example").status == 403
    assert server.runner.calls == []


def test_upload_screenshot_starts_job(server, tmp_path):
    resp = post_form(server, {"token": server.token, "url": POST_URL}, {"image": ("s.png", PNG)})
    assert resp.status == 303
    server.thread.join(10)
    saved = sorted((tmp_path / "paste").glob("screen-*.png"))
    assert len(saved) == 1 and saved[0].read_bytes() == PNG
    assert server.runner.calls[0][2].startswith("/add-from-screen ")
    _, html = get_page(server)
    assert "Claude is making the lead now" in html


def test_upload_bad_file_shows_message(server, tmp_path):
    assert post_form(server, {"token": server.token}, {"image": ("s.gif", b"GIF89a....")}).status == 303
    _, html = get_page(server)
    assert "Not added: the screenshot must be a PNG or JPG image" in html and server.runner.calls == []


def test_csp_allows_only_this_page_script(server):
    resp, html = get_page(server)
    nonce = dashboard.NONCE_RE.search(html).group(1)
    csp = resp.getheader("Content-Security-Policy")
    assert f"script-src 'nonce-{nonce}'" in csp and "default-src 'none'" in csp
    _, html2 = get_page(server)
    assert dashboard.NONCE_RE.search(html2).group(1) != nonce           # new nonce every page
