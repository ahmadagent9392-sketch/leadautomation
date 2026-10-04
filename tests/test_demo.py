"""Tests for Stage 8 demo mode (scripts/demo.py) and the morning digest (scripts/digest.py). SQLite only."""
import re
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import config_check  # noqa: E402
import db  # noqa: E402
import demo  # noqa: E402
import desk as desk_cli  # noqa: E402
import digest  # noqa: E402
from store_sqlite import SqliteStore  # noqa: E402

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+)")


@pytest.fixture(scope="module")
def seeded(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("demo")
    store = SqliteStore(tmp / "demo.db")
    demo.seed(store, tmp / "demo")
    yield store, tmp
    store.close()


@pytest.fixture
def desk(seeded):
    store, tmp = seeded
    return db.Desk(store, config_dir=tmp / "demo" / "config", snapshot_dir=tmp / "demo" / "snapshots")


# ---------- demo data is made up ----------
def test_demo_has_every_stage(seeded):
    store, _ = seeded
    statuses = {l["status"] for l in store.list_leads()}
    assert {"new", "researched", "qualified", "draft_ready", "approved", "contacted", "replied", "won",
            "opted_out", "rejected"} <= statuses
    assert len(store.list_leads()) == len(demo.BUSINESSES)


def test_demo_names_domains_and_emails_are_fake(seeded):
    store, _ = seeded
    for lead in store.list_leads():
        assert lead["company_name"].endswith("(demo)")
        assert lead["company_domain"].endswith(".example")
        assert ".example/" in lead["source_url"]
    texts = [m["body"] for m in store.list_messages()] + [r["body"] for r in store.list_replies()]
    texts += [r["sender"] or "" for r in store.list_replies()]
    texts += [b["value"] for b in store.list_blocks()]
    for lead in store.list_leads():
        texts += [p.get("email") or "" for p in store.people_for(lead["company_id"])]
        texts += [e["url"] + " " + e["quote"] for e in store.evidence_for(lead["id"])]
    for text in texts:
        for domain in EMAIL.findall(text):
            assert domain.endswith(".example"), text
        for url in re.findall(r"https?://([^/\s]+)", text):
            assert url.endswith(".example"), text


def test_demo_config_is_fake_and_ready(seeded):
    _, tmp = seeded
    cfg = tmp / "demo" / "config"
    me = yaml.safe_load((cfg / "me.yaml").read_text(encoding="utf-8"))
    assert me["name"] == "Demo Developer" and "Example Street" in me["postal_address"]
    assert [p.name for p in (cfg / "ideas").glob("*.yaml")] == ["demo-trades-booking.yaml"]   # no real ideas copied
    report = config_check.run_checks(cfg)
    assert not report.errors and not report.warnings                    # strict passes in the demo


def test_demo_refuses_to_fill_twice(seeded):
    store, tmp = seeded
    with pytest.raises(demo.DemoError, match="already has demo data"):
        demo.seed(store, tmp / "demo")


def test_demo_store_never_falls_back_to_real(tmp_path):
    with pytest.raises(db.ConfigError, match="python scripts/demo.py"):
        db.open_store(demo=True, db_path=tmp_path / "missing.db")
    assert not (tmp_path / "missing.db").exists()


def test_desk_cli_demo_flag(seeded, monkeypatch, capsys):
    store, tmp = seeded
    monkeypatch.setattr(db, "DEMO_SQLITE", tmp / "demo.db")
    monkeypatch.setattr(db, "DEMO_DIR", tmp / "demo")
    assert desk_cli.main(["list", "--demo"]) == 0
    out = capsys.readouterr().out
    assert "Brightsmile Dental (demo)" in out and f"{len(demo.BUSINESSES)} lead(s)" in out


# ---------- digest ----------
def test_numbers_and_pipeline(desk):
    total = digest.numbers(desk)
    assert total["won"] == 1 and total["meetings"] == 1
    assert total["positive"] == 2                                       # two made-up positive replies
    assert total["replies"] == 3                                        # + the opt-out
    assert total["sent"] == 5 and total["drafted"] == 8
    assert digest.numbers(desk, 7)["drafted"] == 8
    counts = digest.pipeline(desk)
    assert counts["new"] == 2 and counts["won"] == 1
    assert list(counts) == [s for s in db.STATUSES if s in counts]      # status-flow order


def test_digest_has_all_parts(desk, tmp_path):
    path = digest.write(desk, tmp_path / "digest", notes="## Ahmad must do by hand\n- paste page X")
    assert path.name == f"{desk.today():%Y-%m-%d}.md"
    text = path.read_text(encoding="utf-8")
    for part in ("# Morning digest", "## Needs you today", "Drafts waiting for your OK", "## New leads today",
                 "Riverbend Cleaning (demo)", "## Research and ranking today", "## Best cards now (top 5)",
                 "## Drafts written today", "| Won | 1 | 1 |", "Pipeline: new 2", "## Notes from the morning run",
                 "paste page X", "Nothing was sent"):
        assert part in text, part


def test_digest_cli_demo(seeded, monkeypatch, tmp_path, capsys):
    store, tmp = seeded
    monkeypatch.setattr(db, "DEMO_SQLITE", tmp / "demo.db")
    monkeypatch.setattr(db, "DEMO_DIR", tmp / "demo")
    assert digest.main(["--demo", "--out", str(tmp_path / "d"), "--notes", str(tmp_path / "nope.md")]) == 0
    out = capsys.readouterr().out
    assert "notes file" in out and "Digest written" in out
    assert len(list((tmp_path / "d").glob("*.md"))) == 1


def test_digest_folder_is_git_ignored():
    assert "docs/digest/" in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
