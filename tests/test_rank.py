"""Tests for Stage 4 ranking: set-score, hard gates, priority (SQLite, no internet)."""
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import db  # noqa: E402
import desk as desk_cli  # noqa: E402
import rank  # noqa: E402
from store_sqlite import SqliteStore  # noqa: E402

NOW = datetime(2026, 10, 4, 6, 0, tzinfo=timezone.utc)          # 11:00 in Karachi
LATER = datetime(2026, 12, 20, 6, 0, tzinfo=timezone.utc)       # job post from 2026-09-20 is now > 45 days old
URL = "https://brightsmile.com/careers"
PAGE = """Bright Smile Dental - Careers
We are hiring a Front Desk Coordinator.
Our phones ring all day and we can't keep up with patient calls and web form inquiries.
We are opening a second clinic in November 2026 and need help fast.
One patient wrote: nobody ever called me back about my appointment request.
Contact our office manager Sara Khan at sara@brightsmile.com
Posted: September 20, 2026"""
REVIEW_URL = "https://reviews.example.org/brightsmile"
REVIEW_PAGE = "Review of Bright Smile: I left two voicemails and nobody ever called me back. 2 stars."


@pytest.fixture
def store(tmp_path):
    s = SqliteStore(tmp_path / "r.db")
    yield s
    s.close()


def make_desk(store, tmp_path, now=NOW, config_dir=None):
    return db.Desk(store, config_dir=config_dir, now=lambda: now, snapshot_dir=tmp_path / "snaps")


@pytest.fixture
def desk(store, tmp_path):
    return make_desk(store, tmp_path)


def set_status(store, lead_id, status):
    with store.conn:
        store.conn.execute("UPDATE opportunities SET status = ? WHERE id = ?", (status, lead_id))


def add_proof(desk, lead_id, *, grade="STRONG_SIGNAL", topic="pain", source_type="job_post", url=URL, page=PAGE,
              quote="we can't keep up with patient calls", date="2026-09-20", verify=True):
    status = desk.get(lead_id)["status"]
    if status == "verified":                          # evidence can be added only while researching
        set_status(desk.store, lead_id, "researched")
    sha = desk.save_snapshot(url, page, 200, "page")[0]
    eid, _, _ = desk.add_evidence(lead_id, claim=f"{topic} fact", url=url, quote=quote, grade=grade,
                                  source_type=source_type, topic=topic, observed_at=date, snapshot=sha)
    if verify:
        desk.verify_evidence(eid, grade, "quote found on page")
    set_status(desk.store, lead_id, status)
    return eid


def make_verified(desk, store, *, url="https://brightsmile.com", contact=True, email=None, channel="email",
                  proof=True, **proof_kw):
    """A lead in status 'verified' with one checked STRONG pain proof and an owner contact."""
    lead_id = desk.add_lead(url, "slow replies", channel)
    desk.set_research(lead_id, pattern="missed-leads-slow-replies")
    if proof:
        add_proof(desk, lead_id, **proof_kw)
    if contact:
        kw = {}
        if email:
            ev = add_proof(desk, lead_id, grade="CONFIRMED_FACT", topic="contact", source_type="website",
                           quote="Contact our office manager Sara Khan", date=None)
            kw = dict(email=email, email_status="published", evidence_id=ev)
        desk.set_contact(lead_id, title="Office Manager", name="Sara Khan", role_type="owner", **kw)
    set_status(store, lead_id, "verified")
    return lead_id


def score(desk, lead_id, fit=3, value=2, dq=None):
    desk.set_score(lead_id, fit=fit, value=value, fit_why="phones + web forms, common tools",
                   value_why="hiring a coordinator", disqualifiers=dq)


def only(results, lead_id):
    return next(r for r in results if r.lead_id == lead_id)


# ---------- set-score ----------
def test_set_score_saves_numbers_reasons_and_event(desk, store):
    lead_id = make_verified(desk, store)
    score(desk, lead_id, fit=2, value=3, dq=["  ", "enterprise over 1000 staff"])
    lead = desk.get(lead_id)
    assert (lead["fit"], lead["value_band"]) == (2, 3)
    assert lead["rank_info"]["fit_why"] == "phones + web forms, common tools"
    assert lead["rank_info"]["disqualifiers"] == ["enterprise over 1000 staff"]
    assert any(e["type"] == "scored" for e in store.events_for(lead_id))


@pytest.mark.parametrize("fit,value", [(4, 2), (-1, 2), (2, 0), (2, 4)])
def test_set_score_range_checked(desk, store, fit, value):
    lead_id = make_verified(desk, store)
    with pytest.raises(db.DeskError):
        desk.set_score(lead_id, fit=fit, value=value, fit_why="x", value_why="y")


def test_set_score_needs_reasons(desk, store):
    lead_id = make_verified(desk, store)
    with pytest.raises(db.DeskError, match="fit-why"):
        desk.set_score(lead_id, fit=2, value=2, fit_why=" ", value_why="y")
    with pytest.raises(db.DeskError, match="value-why"):
        desk.set_score(lead_id, fit=2, value=2, fit_why="x", value_why="")


@pytest.mark.parametrize("status", ["new", "researched", "draft_ready", "rejected"])
def test_set_score_wrong_status_refused(desk, store, status):
    lead_id = make_verified(desk, store)
    set_status(store, lead_id, status)
    with pytest.raises(db.InvalidMove):
        score(desk, lead_id)


# ---------- happy path + priority ----------
def test_all_gates_pass_qualifies_and_saves_priority(desk, store):
    lead_id = make_verified(desk, store)
    score(desk, lead_id, fit=3, value=2)
    r = only(rank.run(desk), lead_id)
    assert r.outcome == "qualified"
    assert r.priority == 2 * 3 * 0 * 2 == 0          # no why-now -> urgency 0, sorts last
    lead = desk.get(lead_id)
    assert lead["status"] == "qualified" and lead["priority"] == 0 and lead["urgency"] == 0
    assert lead["rank_info"]["why"] == r.why
    assert any(e["type"] == "ranked" for e in store.events_for(lead_id))


def test_priority_math_and_why_line(desk, store):
    lead_id = make_verified(desk, store)
    add_proof(desk, lead_id, topic="why_now", quote="opening a second clinic in November 2026", grade="STRONG_SIGNAL")
    score(desk, lead_id, fit=3, value=2)
    r = only(rank.run(desk), lead_id)
    assert r.factors["evidence"]["value"] == 2 and r.factors["urgency"]["value"] == 2
    assert r.priority == 2 * 3 * 2 * 2 == 24
    assert r.why.startswith("STRONG proof (job post 2026-09-20) x fit 3 x urgency 2")
    assert r.why.endswith("x value 2 = 24")


def test_confirmed_fact_gives_evidence_3(desk, store):
    lead_id = make_verified(desk, store, grade="CONFIRMED_FACT")
    score(desk, lead_id)
    assert only(rank.run(desk, dry_run=True), lead_id).factors["evidence"]["value"] == 3


def test_urgency_1_from_why_now_text_only(desk, store):
    lead_id = make_verified(desk, store)
    desk.set_research(lead_id, why_now="opening a second clinic")
    score(desk, lead_id, fit=2, value=2)
    r = only(rank.run(desk, dry_run=True), lead_id)
    assert r.factors["urgency"]["value"] == 1
    assert r.priority == 2 * 2 * 1 * 2


def test_urgency_1_from_weak_why_now_proof(desk, store):
    lead_id = make_verified(desk, store)
    add_proof(desk, lead_id, topic="why_now", quote="opening a second clinic in November 2026", grade="WEAK_SIGNAL")
    score(desk, lead_id)
    assert only(rank.run(desk, dry_run=True), lead_id).factors["urgency"]["value"] == 1


def test_two_weak_from_different_sources_pass_with_evidence_1(desk, store):
    lead_id = make_verified(desk, store, grade="WEAK_SIGNAL")
    add_proof(desk, lead_id, grade="WEAK_SIGNAL", source_type="review", url=REVIEW_URL, page=REVIEW_PAGE,
              quote="nobody ever called me back", date="2026-09-25")
    score(desk, lead_id)
    r = only(rank.run(desk, dry_run=True), lead_id)
    assert r.outcome == "qualified" and r.factors["evidence"]["value"] == 1


def test_sorting_priority_then_newest_proof(desk, store):
    a = make_verified(desk, store, url="https://alpha-dental.com")
    b = make_verified(desk, store, url="https://beta-clinic.com", date="2026-09-28")
    c = make_verified(desk, store, url="https://gamma-care.com")
    for lead_id in (a, b, c):
        desk.set_research(lead_id, why_now="growing")
    score(desk, a, fit=2, value=2)
    score(desk, b, fit=2, value=2)
    score(desk, c, fit=3, value=3)
    order = [r.lead_id for r in rank.run(desk)]
    assert order == [c, b, a]            # c highest priority; b and a tie, b has the newer proof


# ---------- each gate alone ----------
def test_no_score_waits_and_stays_verified(desk, store):
    lead_id = make_verified(desk, store)
    r = only(rank.run(desk), lead_id)
    assert r.outcome == "waiting" and r.priority is None
    assert desk.get(lead_id)["status"] == "verified"
    assert [l["id"] for l in rank.need_score(desk)] == [lead_id]


def test_fit_below_2_rejected(desk, store):
    lead_id = make_verified(desk, store)
    score(desk, lead_id, fit=1)
    r = only(rank.run(desk), lead_id)
    assert r.outcome == "rejected" and any("fit 1 is below 2" in f for f in r.fails)
    lead = desk.get(lead_id)
    assert lead["status"] == "rejected" and "fit 1 is below 2" in lead["closed_reason"]


def test_value_below_min_value_band_rejected(desk, store):
    lead_id = make_verified(desk, store)
    score(desk, lead_id, value=1)
    r = only(rank.run(desk), lead_id)
    assert r.outcome == "rejected" and any("min_value_band" in f for f in r.fails)


def test_missing_owner_rejected_with_reason_not_crash(desk, store):
    lead_id = make_verified(desk, store, contact=False)
    score(desk, lead_id)
    r = only(rank.run(desk), lead_id)
    assert r.outcome == "rejected" and any("owner role unknown" in f for f in r.fails)
    assert desk.get(lead_id)["status"] == "rejected"


def test_no_problem_proof_rejected(desk, store):
    lead_id = make_verified(desk, store, proof=False)
    add_proof(desk, lead_id, topic="company", grade="CONFIRMED_FACT", source_type="website", date=None)
    score(desk, lead_id)
    r = only(rank.run(desk), lead_id)
    assert any("no checked problem proof" in f for f in r.fails)


def test_unchecked_proof_does_not_count(desk, store):
    lead_id = make_verified(desk, store, verify=False)
    score(desk, lead_id)
    assert only(rank.run(desk, dry_run=True), lead_id).outcome == "rejected"


def test_two_weak_same_source_type_rejected(desk, store):
    lead_id = make_verified(desk, store, grade="WEAK_SIGNAL")
    add_proof(desk, lead_id, grade="WEAK_SIGNAL", quote="web form inquiries", date="2026-09-21")
    score(desk, lead_id)
    r = only(rank.run(desk, dry_run=True), lead_id)
    assert r.outcome == "rejected" and any("no checked problem proof" in f for f in r.fails)


def test_old_proof_rejects_a_qualified_lead(desk, store, tmp_path):
    lead_id = make_verified(desk, store)
    score(desk, lead_id)
    rank.run(desk)
    assert desk.get(lead_id)["status"] == "qualified"
    later = make_desk(store, tmp_path, now=LATER)
    r = only(rank.run(later), lead_id)
    assert r.outcome == "rejected" and any("too old" in f for f in r.fails)
    assert desk.get(lead_id)["status"] == "rejected"


def test_blocked_domain_rejected(desk, store):
    lead_id = make_verified(desk, store)
    score(desk, lead_id)
    desk.block("brightsmile.com", reason="asked to stop")
    r = only(rank.run(desk), lead_id)
    assert any("block list" in f for f in r.fails)


def test_blocked_contact_email_rejected(desk, store):
    lead_id = make_verified(desk, store, email="sara@brightsmile.com")
    score(desk, lead_id)
    desk.block("sara@brightsmile.com", reason="opt-out")
    r = only(rank.run(desk), lead_id)
    assert any("contact email sara@brightsmile.com is on the block list" in f for f in r.fails)


def test_disqualifier_rejected(desk, store):
    lead_id = make_verified(desk, store)
    score(desk, lead_id, dq=["enterprise over 1000 staff"])
    r = only(rank.run(desk), lead_id)
    assert r.fails == ["disqualifier: enterprise over 1000 staff"]


def test_personal_email_on_email_channel_rejected(desk, store):
    lead_id = make_verified(desk, store, email="sara.khan@gmail.com")
    score(desk, lead_id)
    r = only(rank.run(desk, dry_run=True), lead_id)
    assert any("personal email" in f for f in r.fails)


def test_personal_email_ok_on_other_channel(desk, store):
    lead_id = make_verified(desk, store, email="sara.khan@gmail.com", channel="upwork")
    score(desk, lead_id)
    assert only(rank.run(desk, dry_run=True), lead_id).outcome == "qualified"


def test_no_email_is_only_a_warning(desk, store):
    lead_id = make_verified(desk, store)
    score(desk, lead_id)
    r = only(rank.run(desk, dry_run=True), lead_id)
    assert r.outcome == "qualified" and any("no published email" in w for w in r.warnings)


def test_no_pattern_rejected(desk, store):
    lead_id = make_verified(desk, store)
    with store.conn:
        store.conn.execute("UPDATE opportunities SET pattern_id = NULL WHERE id = ?", (lead_id,))
    score(desk, lead_id)
    assert any("no problem pattern" in f for f in only(rank.run(desk, dry_run=True), lead_id).fails)


# ---------- offer proof gate ----------
def config_copy(tmp_path, offer_status, proof_title="", proof_link=""):
    cfg = tmp_path / "config"
    shutil.copytree(ROOT / "config", cfg)
    offer = yaml.safe_load((cfg / "offer.yaml").read_text(encoding="utf-8"))
    offer["status"] = offer_status
    offer["offers"][0]["proof"] = [{"title": proof_title, "link": proof_link}]
    (cfg / "offer.yaml").write_text(yaml.safe_dump(offer), encoding="utf-8")
    return cfg


def test_offer_not_decided_no_proof_is_warning(store, tmp_path):
    d = make_desk(store, tmp_path, config_dir=config_copy(tmp_path, "not_decided"))
    lead_id = make_verified(d, store)
    score(d, lead_id)
    r = only(rank.run(d), lead_id)
    assert r.outcome == "qualified" and any("no offer proof" in w for w in r.warnings)


def test_offer_decided_without_proof_rejected(store, tmp_path):
    d = make_desk(store, tmp_path, config_dir=config_copy(tmp_path, "decided"))
    lead_id = make_verified(d, store)
    score(d, lead_id)
    r = only(rank.run(d), lead_id)
    assert r.outcome == "rejected" and any("offer has no proof" in f for f in r.fails)


def test_offer_decided_with_proof_passes(store, tmp_path):
    d = make_desk(store, tmp_path, config_dir=config_copy(tmp_path, "decided", "Clinic demo", "https://x.dev/demo"))
    lead_id = make_verified(d, store)
    score(d, lead_id)
    r = only(rank.run(d), lead_id)
    assert r.outcome == "qualified" and not any("offer" in w for w in r.warnings)


# ---------- run options ----------
def test_dry_run_changes_nothing(desk, store):
    lead_id = make_verified(desk, store)
    score(desk, lead_id)
    rank.run(desk, dry_run=True)
    lead = desk.get(lead_id)
    assert lead["status"] == "verified" and lead["priority"] is None


def test_run_one_id_and_wrong_status(desk, store):
    a = make_verified(desk, store, url="https://alpha-dental.com")
    b = make_verified(desk, store, url="https://beta-clinic.com")
    score(desk, a)
    assert [r.lead_id for r in rank.run(desk, lead_id=a)] == [a]
    assert desk.get(b)["status"] == "verified"
    set_status(store, b, "new")
    with pytest.raises(db.InvalidMove):
        rank.run(desk, lead_id=b)


def test_other_statuses_are_ignored(desk, store):
    lead_id = make_verified(desk, store)
    set_status(store, lead_id, "researched")
    assert rank.run(desk) == []


# ---------- command line ----------
def test_cli_set_score_and_rank(tmp_path, capsys, monkeypatch):
    dbfile = tmp_path / "cli.db"
    s = SqliteStore(dbfile)
    now = datetime.now(timezone.utc)                  # the CLI uses the real clock
    d = make_desk(s, tmp_path, now=now)
    lead_id = make_verified(d, s, date=d.today().isoformat())
    s.close()
    monkeypatch.setattr(db, "DEFAULT_SNAPSHOTS", tmp_path / "snaps")
    base = ["--backend", "sqlite", "--db", str(dbfile)]
    assert desk_cli.main(["set-score", str(lead_id), "--fit", "3", "--value", "2", "--fit-why", "a",
                          "--value-why", "b", *base]) == 0
    assert "Saved score" in capsys.readouterr().out
    with pytest.raises(SystemExit):                   # argparse refuses fit 5
        desk_cli.main(["set-score", str(lead_id), "--fit", "5", "--value", "2", "--fit-why", "a",
                       "--value-why", "b", *base])
    capsys.readouterr()
    assert rank.main(["--need-score", *base]) == 0
    assert "No verified lead needs a score" in capsys.readouterr().out
    assert rank.main(base) == 0
    out = capsys.readouterr().out
    assert f"QUALIFIED  #{lead_id}" in out and "1 qualified, 0 rejected, 0 waiting." in out
    assert desk_cli.main(["show", str(lead_id), *base]) == 0
    out = capsys.readouterr().out
    assert "Priority:" in out and "Fit     : 3" in out
