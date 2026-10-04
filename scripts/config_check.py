#!/usr/bin/env python3
"""Check the Opportunity Desk config files.

Usage:
    python scripts/config_check.py            # errors stop, warnings are OK
    python scripts/config_check.py --strict   # warnings also stop (use before outreach)

ERROR   = the system cannot run safely (broken file, safety setting missing).
WARNING = "decide later" item that is still empty (offer, price, proof, address...).
Exit code: 0 = OK, 1 = problems found.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILES = ("me", "offer", "problems", "policy", "cadence", "sources")
REQUIRED_CAPS = (
    "max_new_leads_per_day",
    "max_research_per_day",
    "max_drafts_per_day",
    "max_first_emails_per_day",
    "max_touches_per_lead",
)


def is_empty(value) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    if isinstance(value, (list, dict)):
        return len(value) == 0
    return False


def is_positive_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


class Report:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def error(self, file: str, msg: str) -> None:
        self.errors.append(f"config/{file}.yaml: {msg}")

    def warn(self, file: str, msg: str) -> None:
        self.warnings.append(f"config/{file}.yaml: {msg}")


def load_configs(config_dir: Path, report: Report) -> dict:
    configs: dict = {}
    for name in CONFIG_FILES:
        path = config_dir / f"{name}.yaml"
        if not path.exists():
            report.error(name, "file is missing")
            continue
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            report.error(name, f"YAML is broken: {exc}")
            continue
        if not isinstance(data, dict):
            report.error(name, "file must contain key: value settings")
            continue
        configs[name] = data
    return configs


def check_me(me: dict, r: Report) -> None:
    for key in ("name", "timezone"):
        if is_empty(me.get(key)):
            r.error("me", f"'{key}' is empty")
    for key in ("business_name", "postal_address", "skills", "portfolio_links"):
        if is_empty(me.get(key)):
            r.warn("me", f"'{key}' is empty (set later in dashboard)")


def check_offer(offer: dict, r: Report) -> set[str]:
    offers = offer.get("offers")
    if not isinstance(offers, list) or not offers:
        r.error("offer", "'offers' must be a list with at least one offer")
        return set()
    ids: set[str] = set()
    for i, item in enumerate(offers):
        where = f"offers[{i}]"
        if not isinstance(item, dict) or is_empty(item.get("id")):
            r.error("offer", f"{where}.id is empty")
            continue
        ids.add(item["id"])
        for key in ("name", "problem", "customer", "result"):
            if is_empty(item.get(key)):
                r.warn("offer", f"{where}.{key} is empty (offer not decided yet)")
        if item.get("pricing") != "custom" and not is_positive_int(item.get("price_usd")):
            r.warn("offer", f"{where}.price_usd is not set (or use pricing: custom)")
        proof = [p for p in (item.get("proof") or []) if isinstance(p, dict) and not is_empty(p.get("link"))]
        if not proof:
            r.warn("offer", f"{where}.proof has no link (needed before outreach)")
        if is_empty(item.get("not_a_fit")):
            r.warn("offer", f"{where}.not_a_fit is empty")
    return ids


def check_problems(problems: dict, offer_ids: set[str], r: Report) -> None:
    patterns = problems.get("patterns")
    if not isinstance(patterns, list) or not patterns:
        r.error("problems", "'patterns' must be a list with at least one pattern")
        return
    for i, p in enumerate(patterns):
        where = f"patterns[{i}]"
        if not isinstance(p, dict):
            r.error("problems", f"{where} must be key: value settings")
            continue
        for key in ("id", "keywords", "signals"):
            if is_empty(p.get(key)):
                r.error("problems", f"{where}.{key} is empty")
        if is_empty(p.get("owner_roles")):
            r.warn("problems", f"{where}.owner_roles is empty")
        oid = p.get("offer_id")
        if offer_ids and not is_empty(oid) and oid not in offer_ids:
            r.error("problems", f"{where}.offer_id '{oid}' does not match any offer in offer.yaml")
        for j, s in enumerate(p.get("signals") or []):
            if not isinstance(s, dict) or is_empty(s.get("type")) or not is_positive_int(s.get("decay_days")):
                r.error("problems", f"{where}.signals[{j}] needs 'type' and 'decay_days' > 0")
        rk = p.get("review_keywords")
        if rk is not None and not (isinstance(rk, list) and all(isinstance(k, str) and k.strip() for k in rk)):
            r.error("problems", f"{where}.review_keywords must be a list of words")


def check_ideas(config_dir: Path, offer_ids: set[str], r: Report) -> None:
    """Stage 7: temporary /search-idea patterns in config/ideas/*.yaml (same rules as problems.yaml)."""
    folder = config_dir / "ideas"
    for path in sorted(folder.glob("*.yaml")) if folder.is_dir() else []:
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            r.error("ideas", f"{path.name} is broken YAML: {exc}")
            continue
        sub = Report()
        check_problems({"patterns": [data]}, offer_ids, sub)
        for msg in sub.errors:
            r.error("ideas", f"{path.name}: {msg.split(': ', 1)[-1]}")
        if data.get("id") != f"idea-{path.stem}":
            r.error("ideas", f"{path.name}: id must be 'idea-{path.stem}'")


def check_sources(sources: dict, policy: dict, r: Report) -> None:
    """Stage 7: where the Scout looks, and the Google Maps safety limits."""
    if sources.get("max_age_days") is not None and not is_positive_int(sources.get("max_age_days")):
        r.error("sources", "'max_age_days' must be a whole number above 0")
    for key in ("job_pages", "agency_candidates"):
        for url in sources.get(key) or []:
            if not isinstance(url, str) or not url.startswith(("http://", "https://")):
                r.error("sources", f"{key}: '{url}' must start with http:// or https://")
            elif "linkedin.com" in url.lower():
                r.error("sources", f"{key}: LinkedIn pages are never opened automatically ('{url}')")
    calls = (policy.get("places_api") or {}).get("max_calls_per_day")
    if calls is not None and not is_positive_int(calls):
        r.error("policy", "places_api.max_calls_per_day must be a whole number above 0")
    gmaps = sources.get("gmaps") or {}
    if gmaps.get("searches") is not None and not isinstance(gmaps.get("searches"), list):
        r.error("sources", "gmaps.searches must be a list")
    if not gmaps.get("enabled"):
        return
    limits = policy.get("gmaps_playwright") or {}
    for key in ("max_searches_per_day", "max_businesses_per_day", "cache_days"):
        if not is_positive_int(limits.get(key)):
            r.error("policy", f"gmaps_playwright.{key} must be a whole number above 0")
    wait = limits.get("wait_seconds")
    if not (isinstance(wait, list) and len(wait) == 2 and all(isinstance(w, (int, float)) for w in wait)
            and 1 <= wait[0] <= wait[1]):
        r.error("policy", "gmaps_playwright.wait_seconds must be [min, max] seconds, min at least 1")
    if limits.get("stop_on_captcha") is not True:
        r.error("policy", "gmaps_playwright.stop_on_captcha must be true (never get around a captcha)")


def check_policy(policy: dict, r: Report) -> None:
    if policy.get("sending") != "human_only":
        r.error("policy", "'sending' must be \"human_only\" (the system never sends)")
    caps = policy.get("caps") or {}
    for key in REQUIRED_CAPS:
        if not is_positive_int(caps.get(key)):
            r.error("policy", f"caps.{key} must be a whole number above 0")
    if is_empty(policy.get("channels_allowed")):
        r.error("policy", "'channels_allowed' is empty")
    limits = policy.get("word_limits") or {}
    if not limits:
        r.error("policy", "'word_limits' is empty")
    for channel in policy.get("channels_allowed") or []:
        if not is_positive_int(limits.get(channel)):
            r.error("policy", f"word_limits.{channel} must be a whole number above 0")
    if is_empty(policy.get("banned_phrases")):
        r.error("policy", "'banned_phrases' is empty")
    if is_empty(policy.get("email_footer")):
        r.error("policy", "'email_footer' is empty")


def check_cadence(cadence: dict, r: Report) -> None:
    days = cadence.get("followup_days")
    if not isinstance(days, list) or not days or not all(is_positive_int(d) for d in days):
        r.error("cadence", "'followup_days' must be a list of whole numbers above 0")
    elif days != sorted(days):
        r.error("cadence", "'followup_days' must go from small to big")
    if not is_positive_int(cadence.get("max_touches")):
        r.error("cadence", "'max_touches' must be a whole number above 0")
    # Stage 6 timers: optional (scripts/followups.py has defaults), but must be whole numbers above 0
    for key in ("not_now_default_days", "stale_after_days", "no_response_after_business_days",
                "min_gap_business_days", "ooo_default_business_days"):
        if cadence.get(key) is not None and not is_positive_int(cadence.get(key)):
            r.error("cadence", f"'{key}' must be a whole number above 0")


def run_checks(config_dir: Path = ROOT / "config") -> Report:
    report = Report()
    configs = load_configs(config_dir, report)
    if "me" in configs:
        check_me(configs["me"], report)
    offer_ids = check_offer(configs["offer"], report) if "offer" in configs else set()
    if "problems" in configs:
        check_problems(configs["problems"], offer_ids, report)
    check_ideas(config_dir, offer_ids, report)
    if "policy" in configs:
        check_policy(configs["policy"], report)
    if "sources" in configs:
        check_sources(configs["sources"], configs.get("policy") or {}, report)
    if "cadence" in configs:
        check_cadence(configs["cadence"], report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check Opportunity Desk config files.")
    parser.add_argument("--strict", action="store_true", help="treat warnings as errors (use before outreach)")
    parser.add_argument("--config-dir", type=Path, default=ROOT / "config")
    args = parser.parse_args(argv)

    report = run_checks(args.config_dir)
    for msg in report.errors:
        print(f"ERROR    {msg}")
    for msg in report.warnings:
        print(f"WARNING  {msg}")

    failed = bool(report.errors) or (args.strict and bool(report.warnings))
    if failed:
        extra = " (strict mode: warnings count as errors)" if args.strict and report.warnings else ""
        print(f"\nFAILED: {len(report.errors)} error(s), {len(report.warnings)} warning(s){extra}.")
        return 1
    print(f"\nOK: config is safe to run. {len(report.warnings)} warning(s) = things to decide later.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
