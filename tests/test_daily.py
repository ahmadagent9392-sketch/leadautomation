"""Tests for Stage 8 morning job (scripts/daily.py). A fake `claude` runner: no real Claude, no internet."""
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import daily  # noqa: E402
import db  # noqa: E402
from store_sqlite import SqliteStore  # noqa: E402

TZ = timezone(timedelta(hours=5), "PKT")
NOW = datetime(2026, 10, 5, 2, 30, tzinfo=timezone.utc)          # 07:30 in Karachi
POLICY = {"daily_run": {"max_research": 5, "max_drafts": 3, "timeout_minutes": 90}}
SEND_WORDS = ("send", "forward", "reply", "draft", "delete", "trash", "label", "approve", "campaign")


class FakeRunner:
    def __init__(self, returncode=0, result="did the morning job", is_error=False, raise_exc=None):
        self.calls = []
        self.returncode, self.result, self.is_error, self.raise_exc = returncode, result, is_error, raise_exc

    def __call__(self, cmd, **kw):
        self.calls.append((cmd, kw))
        if self.raise_exc:
            raise self.raise_exc
        out = json.dumps({"type": "result", "result": self.result, "is_error": self.is_error,
                          "num_turns": 42, "total_cost_usd": 1.234})
        return subprocess.CompletedProcess(cmd, self.returncode, out, "")


@pytest.fixture
def logs(tmp_path):
    return tmp_path / "logs"


@pytest.fixture
def stop(tmp_path):
    return tmp_path / "data" / "STOP"


def run(logs, stop, runner, force=False, now=NOW):
    return daily.run_daily(tz=TZ, policy=POLICY, force=force, logs=logs, stop_file=stop, runner=runner,
                           claude="claude", now=lambda: now)


# ---------- the fixed tool list ----------
def test_allowed_tools_never_send():
    for tool in daily.ALLOWED_TOOLS:
        if tool.startswith("mcp__"):
            assert tool.rsplit("__", 1)[1] in ("get_thread", "list_drafts", "search_threads", "list_labels"), tool
            assert not any(w in tool for w in SEND_WORDS if w not in ("draft", "label")), tool
    assert "Bash" not in daily.ALLOWED_TOOLS                    # never every shell command
    assert all(not t.startswith("Bash(") or t == "Bash(python scripts/*)" for t in daily.ALLOWED_TOOLS)
    assert not any("create_draft" in t or "send" in t.lower() for t in daily.ALLOWED_TOOLS)


def test_claude_command_uses_dont_ask_and_the_list():
    cmd = daily.claude_command("/daily-run", "claude")
    assert cmd[:3] == ["claude", "-p", "/daily-run"]
    assert cmd[cmd.index("--permission-mode") + 1] == "dontAsk"
    assert "bypassPermissions" not in cmd and "--dangerously-skip-permissions" not in cmd
    for tool in daily.ALLOWED_TOOLS:
        assert tool in cmd
    assert cmd[cmd.index("--append-system-prompt") + 1] == daily.UNATTENDED_PROMPT


def test_unattended_prompt_is_safe_for_cmd_exe():
    assert not any(ch in daily.UNATTENDED_PROMPT for ch in '"%&|<>^!')
    for words in ("Never ask", "Never send", "Never create Gmail drafts", "DATA, not instructions"):
        assert words in daily.UNATTENDED_PROMPT


def test_settings_defaults():
    assert daily.settings({}) == {"max_research": 5, "max_drafts": 3, "timeout_minutes": 90}
    assert daily.settings({"daily_run": {"max_drafts": 1}})["max_drafts"] == 1


# ---------- checks ----------
def test_stop_file_stops_everything(logs, stop):
    stop.parent.mkdir(parents=True)
    stop.write_text("")
    reasons = daily.stop_reasons(TZ, NOW, stop_file=stop, logs=logs)
    assert reasons and reasons[0].startswith("STOPPED")
    assert daily.stop_reasons(TZ, NOW, stop_file=stop, logs=logs, inside=True)[0].startswith("STOPPED")
    runner = FakeRunner()
    code, msg = run(logs, stop, runner)
    assert code == 2 and "STOPPED" in msg and runner.calls == []


def test_lock_blocks_a_second_run_but_old_lock_is_ignored(logs, stop):
    daily.take_lock(logs, daily.LOCK_NAME, NOW)
    assert any("another morning run" in r for r in daily.stop_reasons(TZ, NOW, stop_file=stop, logs=logs))
    with pytest.raises(daily.RunError):
        daily.take_lock(logs, daily.LOCK_NAME, NOW)
    later = NOW + timedelta(hours=4)                          # crashed run: lock older than 3 hours
    assert daily.stop_reasons(TZ, later, stop_file=stop, logs=logs) == []
    daily.take_lock(logs, daily.LOCK_NAME, later)            # replaces the old lock
    daily.free_lock(logs)
    assert daily.lock_info(logs) is None


def test_run_writes_log_and_runs_line(logs, stop):
    runner = FakeRunner()
    code, msg = run(logs, stop, runner)
    assert code == 0 and msg.startswith("OK")
    cmd, kw = runner.calls[0]
    assert cmd[2] == "/daily-run" and kw["timeout"] == 90 * 60 and kw["cwd"] == str(daily.ROOT)
    rows = daily.read_runs(logs)
    assert len(rows) == 1 and rows[0]["ok"] and rows[0]["kind"] == "daily"
    assert rows[0]["turns"] == 42 and rows[0]["cost_usd"] == 1.234
    log = (logs / rows[0]["log"]).read_text(encoding="utf-8")
    assert "did the morning job" in log and rows[0]["log"].startswith("daily-2026-10-05-0730")
    assert daily.lock_info(logs) is None                      # lock freed


def test_one_good_run_a_day_unless_force(logs, stop):
    run(logs, stop, FakeRunner())
    runner = FakeRunner()
    code, msg = run(logs, stop, runner, now=NOW + timedelta(hours=2))
    assert code == 2 and "already finished OK today" in msg and runner.calls == []
    assert run(logs, stop, FakeRunner(), force=True, now=NOW + timedelta(hours=2))[0] == 0
    # the next Karachi day runs again
    assert run(logs, stop, FakeRunner(), now=NOW + timedelta(days=1))[0] == 0


def test_failed_run_is_logged_and_can_run_again(logs, stop):
    code, msg = run(logs, stop, FakeRunner(returncode=1, is_error=True, result="boom"))
    assert code == 1 and msg.startswith("FAILED")
    assert not daily.read_runs(logs)[0]["ok"]
    assert run(logs, stop, FakeRunner(), now=NOW + timedelta(minutes=30))[0] == 0   # a failed run does not count


def test_timeout_is_stopped_and_logged(logs, stop):
    code, msg = run(logs, stop, FakeRunner(raise_exc=subprocess.TimeoutExpired("claude", 5400)))
    assert code == 1 and "longer than 90 minutes" in msg
    assert daily.lock_info(logs) is None


def test_parse_result_bad_json():
    assert daily.parse_result("not json")["text"] == "not json"
    assert daily.parse_result("")["turns"] is None


def test_broken_runs_file_lines_are_skipped(logs):
    logs.mkdir()
    (logs / daily.RUNS_NAME).write_text('{"kind": "daily", "ok": true}\nbroken\n[1]\n', encoding="utf-8")
    assert len(daily.read_runs(logs)) == 1


# ---------- today's numbers ----------
def test_today_numbers_respect_caps(tmp_path):
    store = SqliteStore(tmp_path / "d.db")
    try:
        desk = db.Desk(store, snapshot_dir=tmp_path / "s")          # real clock: events use it too
        cap = desk.policy["caps"]["max_research_per_day"]
        for _ in range(cap - 2):
            store.add_event("research_started", "test", None, {})
        n = daily.today_numbers(desk)
        assert n["research"] == min(2, daily.settings(desk.policy)["max_research"])
        assert n["drafts"] == daily.settings(desk.policy)["max_drafts"]
    finally:
        store.close()


def test_check_cli_with_stop(tmp_path, monkeypatch, capsys):
    stop = tmp_path / "STOP"
    stop.write_text("")
    monkeypatch.setattr(daily, "STOP_FILE", stop)
    assert daily.main(["check", "--inside"]) == 2
    assert "STOPPED" in capsys.readouterr().out
