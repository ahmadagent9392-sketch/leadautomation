"""Tests for the Stage 3 research rules in scripts/db.py and the desk.py research commands (SQLite, no internet)."""
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import db  # noqa: E402
import desk as desk_cli  # noqa: E402
from store_sqlite import SqliteStore  # noqa: E402

NOW = datetime(2026, 10, 4, 6, 0, tzinfo=timezone.utc)          # 11:00 in Karachi
PAGE = """Bright Smile Dental - Careers
We are hiring a Front Desk Coordinator.
Our phones ring all day and we can't keep up with patient calls and web form inquiries.
Contact our office manager Sara Khan at sara@brightsmile.com
Posted: September 20, 2026"""
URL = "https://brightsmile.com/careers"
QUOTE = "we can't keep up with patient calls"


@pytest.fixture
def store(tmp_path):
    s = SqliteStore(tmp_path / "r.db")
    yield s
    s.close()


@pytest.fixture
def desk(store, tmp_path):
    return db.Desk(store, now=lambda: NOW, snapshot_dir=tmp_path / "snaps")


@pytest.fixture
def lead(desk):
    lead_id = desk.add_lead("https://brightsmile.com", "slow replies", "email")
    desk.set_research(lead_id, pattern="missed-leads-slow-replies")
    return lead_id


@pytest.fixture
def sha(desk):
    return desk.save_snapshot(URL, PAGE, 200, "Careers")[0]


def pain(desk, lead_id, sha, **kw):
    args = dict(claim="They cannot keep up with patient calls", url=URL, quote=QUOTE, grade="STRONG_SIGNAL",
                source_type="job_post", topic="pain", observed_at="2026-09-20", snapshot=sha)
    args.update(kw)
    return desk.add_evidence(lead_id, **args)


def set_status(store, lead_id, status):
    with store.conn:
        store.conn.execute("UPDATE opportunities SET status = ? WHERE id = ?", (status, lead_id))


# ---------- start-research ----------
def test_start_research_rounds(desk, store, lead):
    assert desk.start_research(lead) == 1
    with pytest.raises(db.InvalidMove):          # round 2 only after the lead is 'researched'... or still new
        set_status(store, lead, "verified")
        desk.start_research(lead)
    set_status(store, lead, "researched")
    assert desk.start_research(lead) == 2
    with pytest.raises(db.CapReached, match="max 2"):
        desk.start_research(lead)


def test_first_round_needs_new(desk, store, lead):
    set_status(store, lead, "researched")
    with pytest.raises(db.InvalidMove):
        desk.start_research(lead)


def test_research_daily_cap(desk, store):
    desk.policy["caps"]["max_research_per_day"] = 2
    desk.policy["caps"]["max_new_leads_per_day"] = 10
    ids = [desk.add_lead(f"https://shop{i}-alpha{i}.com", "n", "email") for i in range(3)]
    desk.start_research(ids[0])
    desk.start_research(ids[1])
    with pytest.raises(db.CapReached, match="2 research runs today"):
        desk.start_research(ids[2])


def test_blocked_lead_not_researched(desk, lead):
    desk.block("brightsmile.com", "opt_out")
    with pytest.raises(db.Blocked):
        desk.start_research(lead)


# ---------- snapshots ----------
def test_snapshot_saved_once_and_readable(desk, store, sha):
    assert desk.snapshot_text(sha) == PAGE
    assert desk.save_snapshot(URL, PAGE, 200, "Careers")[2] is False
    assert store.get_snapshot(sha)["url"] == URL


def test_bad_or_missing_snapshot(desk, store, sha):
    with pytest.raises(db.DeskError, match="not a snapshot"):
        desk.snapshot_text("abc")
    with pytest.raises(db.NotFound, match="not in the database"):
        desk.snapshot_text("0" * 64)
    desk.snapshot_path(sha).rename(desk.snapshot_dir / "moved.txt")
    with pytest.raises(db.NotFound, match="missing on this PC"):
        desk.snapshot_text(sha)


# ---------- add-evidence ----------
def test_real_quote_keeps_grade(desk, store, lead, sha):
    eid, grade, warnings = pain(desk, lead, sha)
    assert grade == "STRONG_SIGNAL" and warnings == []
    ev = store.get_evidence(eid)
    assert ev["snapshot_sha256"] == sha and ev["topic"] == "pain" and ev["verified"] is False
    assert store.events_for(lead)[-1]["type"] == "evidence_added"


def test_invented_quote_becomes_unknown(desk, store, lead, sha):
    eid, grade, warnings = pain(desk, lead, sha, quote="We lost 50 patients last month because nobody answered")
    assert grade == "UNKNOWN" and "NOT FOUND" in warnings[0]
    assert store.get_evidence(eid)["grade"] == "UNKNOWN"


def test_paraphrased_quote_becomes_unknown(desk, lead, sha):
    assert pain(desk, lead, sha, quote="they cannot handle all the calls from patients")[1] == "UNKNOWN"


def test_quoted_grade_needs_snapshot_and_real_quote(desk, lead, sha):
    with pytest.raises(db.DeskError, match="needs --snapshot"):
        pain(desk, lead, None)
    with pytest.raises(db.DeskError, match="at least 15"):
        pain(desk, lead, sha, quote="phones")
    with pytest.raises(db.DeskError, match="max 300"):
        pain(desk, lead, sha, quote="x" * 301)


def test_snapshot_of_other_page_refused(desk, lead, sha):
    with pytest.raises(db.DeskError, match="not https://other.com"):
        pain(desk, lead, sha, url="https://other.com/page")
    # small URL differences are the same page
    assert pain(desk, lead, sha, url="http://www.brightsmile.com/careers/")[1] == "STRONG_SIGNAL"


def test_evidence_input_checks(desk, lead, sha):
    with pytest.raises(db.DeskError, match="unknown grade"):
        pain(desk, lead, sha, grade="VERY_STRONG")
    with pytest.raises(db.DeskError, match="unknown topic"):
        pain(desk, lead, sha, topic="gossip")
    with pytest.raises(db.DeskError, match="in the future"):
        pain(desk, lead, sha, observed_at="2026-12-01")
    with pytest.raises(db.DeskError, match="YYYY-MM-DD"):
        pain(desk, lead, sha, observed_at="20 Sept")


def test_inference_needs_depends_on(desk, lead, sha):
    with pytest.raises(db.DeskError, match="depends-on"):
        desk.add_evidence(lead, claim="Probably loses patients", url=URL, quote="", grade="INFERENCE",
                          source_type="job_post", topic="impact")
    e1, _, _ = pain(desk, lead, sha)
    eid, grade, _ = desk.add_evidence(lead, claim="Probably loses patients", url=URL, quote="",
                                      grade="INFERENCE", source_type="job_post", topic="impact", depends_on=[e1])
    assert grade == "INFERENCE"
    with pytest.raises(db.DeskError, match="not evidence of lead"):
        desk.add_evidence(lead, claim="x", url=URL, quote="", grade="INFERENCE", source_type="job_post",
                          topic="impact", depends_on=[999])


def test_no_evidence_on_closed_or_late_lead(desk, store, lead, sha):
    set_status(store, lead, "qualified")
    with pytest.raises(db.InvalidMove):
        pain(desk, lead, sha)


# ---------- verify-evidence ----------
def test_checker_confirms(desk, store, lead, sha):
    eid, _, _ = pain(desk, lead, sha)
    before, final, reasons = desk.verify_evidence(eid, "STRONG_SIGNAL", "quote is in the job post")
    assert final == "STRONG_SIGNAL" and reasons == []
    ev = store.get_evidence(eid)
    assert ev["verified"] is True and ev["verifier_note"] == "quote is in the job post"
    assert store.events_for(lead)[-1]["type"] == "evidence_verified"


def test_checker_cannot_raise(desk, store, lead, sha):
    eid, _, _ = pain(desk, lead, sha, grade="WEAK_SIGNAL")
    _, final, reasons = desk.verify_evidence(eid, "CONFIRMED_FACT", "looks great")
    assert final == "WEAK_SIGNAL" and "cannot raise" in reasons[0]


def test_checker_can_lower_and_fix_claim(desk, store, lead, sha):
    eid, _, _ = pain(desk, lead, sha)
    _, final, _ = desk.verify_evidence(eid, "WEAK_SIGNAL", "claim said too much", claim="Busy phones")
    ev = store.get_evidence(eid)
    assert final == "WEAK_SIGNAL" and ev["claim"] == "Busy phones"


def test_stale_proof_lowered(desk, lead, sha):
    eid, _, _ = pain(desk, lead, sha, observed_at="2026-06-01")      # job post, 45 days fresh
    _, final, reasons = desk.verify_evidence(eid, "STRONG_SIGNAL", "ok")
    assert final == "WEAK_SIGNAL" and "older than 45 days" in reasons[0]


def test_quote_gone_from_new_snapshot(desk, lead, sha):
    eid, _, _ = pain(desk, lead, sha)
    new_sha = desk.save_snapshot(URL, "Bright Smile Dental - Careers\nThis job is closed.", 200, "Careers")[0]
    _, final, reasons = desk.verify_evidence(eid, "STRONG_SIGNAL", "re-checked", snapshot=new_sha)
    assert final == "UNKNOWN" and "quote not found" in reasons[0]


def test_inference_on_unchecked_facts_becomes_unknown(desk, lead, sha):
    e1, _, _ = pain(desk, lead, sha)
    e2, _, _ = desk.add_evidence(lead, claim="Probably loses patients", url=URL, quote="", grade="INFERENCE",
                                 source_type="job_post", topic="impact", depends_on=[e1])
    assert desk.verify_evidence(e2, "INFERENCE", "x")[1] == "UNKNOWN"      # E1 not checked yet
    e3, _, _ = desk.add_evidence(lead, claim="Probably loses patients", url=URL, quote="", grade="INFERENCE",
                                 source_type="job_post", topic="impact", depends_on=[e1])
    desk.verify_evidence(e1, "STRONG_SIGNAL", "ok")
    assert desk.verify_evidence(e3, "INFERENCE", "ok")[1] == "INFERENCE"


def test_verify_needs_note_and_known_evidence(desk, lead, sha):
    eid, _, _ = pain(desk, lead, sha)
    with pytest.raises(db.DeskError, match="note is required"):
        desk.verify_evidence(eid, "STRONG_SIGNAL", " ")
    with pytest.raises(db.NotFound):
        desk.verify_evidence(999, "STRONG_SIGNAL", "x")


# ---------- set-contact ----------
def test_contact_with_published_email(desk, store, lead, sha):
    eid, _, _ = desk.add_evidence(lead, claim="Office manager email is published", url=URL,
                                  quote="Contact our office manager Sara Khan at sara@brightsmile.com",
                                  grade="CONFIRMED_FACT", source_type="job_post", topic="contact", snapshot=sha)
    pid = desk.set_contact(lead, title="Office Manager", name="Sara Khan", role_type="owner",
                           email="Sara@BrightSmile.com", email_status="published", evidence_id=eid)
    person = store.people_for(store.get_lead(lead)["company_id"])[0]
    assert person["email"] == "sara@brightsmile.com" and person["email_status"] == "published"
    assert store.get_lead(lead)["owner_person_id"] == pid


def test_guessed_email_refused(desk, lead, sha):
    with pytest.raises(db.DeskError, match="Never save a guessed email"):
        desk.set_contact(lead, title="Owner", email="owner@brightsmile.com", email_status="inferred")
    with pytest.raises(db.DeskError, match="Never save a guessed email"):
        desk.set_contact(lead, title="Owner", email="owner@brightsmile.com")
    with pytest.raises(db.DeskError, match="needs --evidence"):
        desk.set_contact(lead, title="Owner", email="owner@brightsmile.com", email_status="published")


def test_blocked_email_refused(desk, lead, sha):
    eid, _, _ = pain(desk, lead, sha, topic="contact")
    desk.block("sara@brightsmile.com", "opt_out")
    with pytest.raises(db.Blocked):
        desk.set_contact(lead, title="Office Manager", email="sara@brightsmile.com", email_status="published",
                         evidence_id=eid)


def test_contact_without_email_ok(desk, store, lead):
    desk.set_contact(lead, title="Owner", role_type="owner", profile_url="https://linkedin.com/in/x")
    assert store.people_for(store.get_lead(lead)["company_id"])[0]["profile_url"] == "https://linkedin.com/in/x"
    with pytest.raises(db.DeskError, match="title is required"):
        desk.set_contact(lead, title=" ")


# ---------- set-research ----------
def test_set_research_saves_lead_and_company(desk, store, lead):
    changed = desk.set_research(lead, why_now="hiring now", unknowns=["owner name ", "", "size"],
                                channel="upwork", industry="Dental", size="11-50", country="US",
                                company_name="Bright Smile Dental")
    assert "why_now" in changed and "industry" in changed
    row = store.get_lead(lead)
    assert row["why_now"] == "hiring now" and row["unknowns"] == ["owner name", "size"]
    assert row["channel"] == "upwork_proposal" and row["company_name"] == "Bright Smile Dental"
    assert store.events_for(lead)[-1]["type"] == "research_saved"


def test_set_research_checks(desk, store, lead):
    with pytest.raises(db.DeskError, match="unknown pattern"):
        desk.set_research(lead, pattern="crypto")
    with pytest.raises(db.DeskError, match="nothing to save"):
        desk.set_research(lead)
    with pytest.raises(db.DeskError, match="not a company website"):
        desk.set_research(lead, domain="upwork.com")
    other = desk.add_lead("https://other-clinic.com", "n", "email")
    with pytest.raises(db.DuplicateLead):
        desk.set_research(other, domain="https://www.brightsmile.com/about")
    desk.block("spam.com", "manual")
    with pytest.raises(db.Blocked):
        desk.set_research(other, domain="spam.com")


def test_platform_lead_gets_domain(desk, store):
    lead_id = desk.add_lead("https://www.upwork.com/jobs/~01", "data entry", "upwork")
    desk.set_research(lead_id, domain="acme-shop.com", company_name="Acme Shop")
    row = store.get_lead(lead_id)
    assert row["company_domain"] == "acme-shop.com" and row["company_name"] == "Acme Shop"


# ---------- gate: researched -> verified ----------
def test_gate_blocks_without_proof(desk, store, lead, sha):
    set_status(store, lead, "researched")
    with pytest.raises(db.InvalidMove, match="no checked problem proof"):
        desk.move(lead, "verified", "checker PASS")
    eid, _, _ = pain(desk, lead, sha)                   # saved but not checked yet
    with pytest.raises(db.InvalidMove, match="no checked problem proof"):
        desk.move(lead, "verified", "checker PASS")
    desk.verify_evidence(eid, "STRONG_SIGNAL", "ok")
    with pytest.raises(db.InvalidMove, match="no contact person"):
        desk.move(lead, "verified", "checker PASS")
    desk.set_contact(lead, title="Owner", role_type="owner")
    assert desk.move(lead, "verified", "checker PASS") == ("researched", "verified")


def test_gate_company_facts_do_not_count(desk, store, lead, sha):
    eid, _, _ = pain(desk, lead, sha, topic="company")
    desk.verify_evidence(eid, "STRONG_SIGNAL", "ok")
    desk.set_contact(lead, title="Owner")
    set_status(store, lead, "researched")
    with pytest.raises(db.InvalidMove, match="no checked problem proof"):
        desk.move(lead, "verified", "x")


def test_gate_two_weak_from_different_sources(desk, store, lead, sha):
    desk.set_contact(lead, title="Owner")
    e1, _, _ = pain(desk, lead, sha, grade="WEAK_SIGNAL")
    e2, _, _ = pain(desk, lead, sha, grade="WEAK_SIGNAL")
    desk.verify_evidence(e1, "WEAK_SIGNAL", "ok")
    desk.verify_evidence(e2, "WEAK_SIGNAL", "ok")
    set_status(store, lead, "researched")
    with pytest.raises(db.InvalidMove):                  # both are job posts
        desk.move(lead, "verified", "x")
    e3, _, _ = pain(desk, lead, sha, grade="WEAK_SIGNAL", source_type="website")
    desk.verify_evidence(e3, "WEAK_SIGNAL", "ok")
    assert desk.move(lead, "verified", "x")[1] == "verified"


def test_fake_lead_fails(desk, store, lead, sha):
    """Roadmap check: a lead with an invented claim can never reach 'verified'."""
    desk.start_research(lead)
    eid, grade, _ = pain(desk, lead, sha, claim="They lose $10k a month",
                         quote="we lose about ten thousand dollars every month on missed calls")
    assert grade == "UNKNOWN"
    desk.set_contact(lead, title="Owner")
    desk.move(lead, "researched", "researcher done")
    desk.verify_evidence(eid, "STRONG_SIGNAL", "trying to pass it anyway")
    with pytest.raises(db.InvalidMove, match="no checked problem proof"):
        desk.move(lead, "verified", "x")


# ---------- command line ----------
@pytest.fixture
def run(tmp_path, capsys, monkeypatch):
    db_file, snaps = tmp_path / "cli.db", tmp_path / "snaps"
    monkeypatch.setattr(db, "DEFAULT_SNAPSHOTS", snaps)

    def _run(*args):
        code = desk_cli.main([*args, "--backend", "sqlite", "--db", str(db_file)])
        return code, capsys.readouterr().out
    return _run


def test_cli_research_flow(run, tmp_path):
    store = SqliteStore(tmp_path / "cli.db")
    d = db.Desk(store, snapshot_dir=tmp_path / "snaps")
    sha = d.save_snapshot(URL, PAGE, 200, "Careers")[0]
    store.close()

    assert run("add-lead", "--url", "https://brightsmile.com", "--note", "n", "--channel", "email")[0] == 0
    code, out = run("start-research", "1")
    assert code == 0 and "round 1 of 2" in out
    code, out = run("add-evidence", "1", "--claim", "Cannot keep up with calls", "--url", URL, "--quote", QUOTE,
                    "--source-type", "job_post", "--topic", "pain", "--grade", "STRONG_SIGNAL",
                    "--snapshot", sha, "--date", date.today().isoformat())
    assert code == 0 and "Saved evidence E1 [STRONG_SIGNAL]" in out
    code, out = run("add-evidence", "1", "--claim", "Invented", "--url", URL, "--quote",
                    "we lose ten patients every single week", "--source-type", "job_post", "--topic", "pain",
                    "--grade", "STRONG_SIGNAL", "--snapshot", sha)
    assert code == 0 and "NOT FOUND" in out and "E2 [UNKNOWN]" in out
    code, out = run("set-contact", "1", "--title", "Office Manager", "--role-type", "owner",
                    "--profile-url", "https://linkedin.com/in/x")
    assert code == 0 and "P1" in out and "by hand" in out
    code, out = run("set-research", "1", "--pattern", "missed-leads-slow-replies", "--why-now", "hiring now",
                    "--unknowns", "team size; owner name")
    assert code == 0 and "pattern_id" in out
    assert run("move", "1", "researched", "--reason", "researcher done")[0] == 0
    code, out = run("move", "1", "verified", "--reason", "checker PASS")
    assert code == 1 and "no checked problem proof" in out
    code, out = run("verify-evidence", "E1", "--grade", "STRONG_SIGNAL", "--note", "quote found")
    assert code == 0 and "STRONG_SIGNAL -> STRONG_SIGNAL" in out
    assert run("move", "1", "verified", "--reason", "checker PASS")[0] == 0
    code, out = run("show", "1")
    assert "Pattern : missed-leads-slow-replies" in out and "Unknown : owner name" in out
    assert "(pain, job_post, checked)" in out and "checker: quote found" in out
    assert "[owner]" in out


def test_cli_errors(run):
    run("add-lead", "--url", "https://brightsmile.com", "--note", "n", "--channel", "email")
    code, out = run("add-evidence", "1", "--claim", "x", "--url", URL, "--quote", QUOTE, "--source-type",
                    "job_post", "--topic", "pain", "--grade", "STRONG_SIGNAL")
    assert code == 1 and "needs --snapshot" in out
    code, out = run("verify-evidence", "Ex", "--grade", "UNKNOWN", "--note", "n")
    assert code == 1 and "not an evidence id" in out
    code, out = run("set-contact", "1", "--title", "Owner", "--email", "a@b.com")
    assert code == 1 and "guessed email" in out
