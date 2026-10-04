"""Tests for the scripts/desk.py command line (SQLite backend on a temp file)."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import desk  # noqa: E402


@pytest.fixture
def run(tmp_path, capsys):
    db_file = tmp_path / "cli.db"

    def _run(*args):
        code = desk.main([*args, "--backend", "sqlite", "--db", str(db_file)])
        return code, capsys.readouterr().out
    return _run


def test_full_manual_flow(run):
    assert run("init")[0] == 0
    assert run("add-lead", "--url", "https://alpha-dental.com", "--note", "slow replies", "--channel", "email")[0] == 0
    assert run("add-lead", "--url", "https://www.upwork.com/jobs/~01", "--note", "data entry",
               "--channel", "upwork")[0] == 0
    assert run("add-lead", "--url", "https://bravo-plumbing.com", "--note", "missed calls",
               "--channel", "referral")[0] == 0

    code, out = run("list")
    assert code == 0 and "3 lead(s)" in out
    assert "Alpha Dental" in out and "upwork_proposal" in out and "referral_ask" in out

    code, out = run("show", "1")
    assert code == 0 and "alpha-dental.com" in out and "Next    : researched, rejected, opted_out" in out

    code, out = run("move", "1", "won", "--reason", "test")
    assert code == 1 and "REFUSED" in out and "new -> won is not allowed" in out

    code, out = run("move", "1", "researched", "--reason", "looked at site")
    assert code == 0 and "new -> researched" in out

    code, out = run("history", "1")
    assert "lead added" in out and "new -> researched" in out and "looked at site" in out

    code, out = run("list", "--status", "researched")
    assert "1 lead(s)" in out and "Alpha Dental" in out and "Bravo" not in out


def test_duplicate_refused(run):
    run("add-lead", "--url", "https://alpha.com", "--note", "n", "--channel", "email")
    code, out = run("add-lead", "--url", "https://www.alpha.com/contact", "--note", "n", "--channel", "email")
    assert code == 1 and "REFUSED" in out and "already exists" in out


def test_block_and_blocked(run):
    run("add-lead", "--url", "https://alpha.com", "--note", "n", "--channel", "email")
    code, out = run("block", "Owner@Alpha.com", "--reason", "opt_out")
    assert code == 0 and "Blocked email owner@alpha.com" in out and "#1" in out
    code, out = run("blocked")
    assert "owner@alpha.com" in out and "opt_out" in out
    run("block", "spam.com", "--reason", "manual")
    code, out = run("add-lead", "--url", "https://spam.com", "--note", "n", "--channel", "email")
    assert code == 1 and "block list" in out


def test_errors_are_clear(run):
    code, out = run("show", "42")
    assert code == 1 and "lead #42 not found" in out
    code, out = run("list", "--status", "nope")
    assert code == 1 and "unknown status" in out
    code, out = run("add-lead", "--url", "https://alpha.com", "--note", "n", "--channel", "fax")
    assert code == 1 and "unknown channel" in out


def test_reason_is_required(run):
    run("add-lead", "--url", "https://alpha.com", "--note", "n", "--channel", "email")
    with pytest.raises(SystemExit):
        desk.main(["move", "1", "researched", "--backend", "sqlite"])
