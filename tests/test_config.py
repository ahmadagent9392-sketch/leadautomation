"""Tests for scripts/config_check.py."""
import shutil
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import config_check  # noqa: E402


@pytest.fixture
def cfg(tmp_path):
    """A copy of the real config folder that each test can change."""
    target = tmp_path / "config"
    shutil.copytree(ROOT / "config", target)
    return target


def edit(cfg_dir: Path, name: str, change) -> None:
    path = cfg_dir / f"{name}.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    change(data)
    path.write_text(yaml.safe_dump(data), encoding="utf-8")


def test_real_config_passes():
    report = config_check.run_checks()
    assert report.errors == []
    assert config_check.main([]) == 0


def test_empty_offer_is_only_a_warning(cfg):
    report = config_check.run_checks(cfg)
    assert report.errors == []
    assert any("offers[0].problem is empty" in w for w in report.warnings)
    assert config_check.main(["--config-dir", str(cfg)]) == 0


def test_strict_mode_fails_on_warnings(cfg):
    assert config_check.main(["--config-dir", str(cfg), "--strict"]) == 1


def test_strict_mode_passes_when_everything_is_filled(cfg):
    def fill_me(d):
        d.update(business_name="Demo Studio", postal_address="1 Demo St", skills=["n8n"],
                 portfolio_links=["https://example.com"])

    def fill_offer(d):
        d["offers"][0].update(name="Demo", problem="slow replies", customer="small shops",
                              result="auto replies", proof=[{"title": "demo", "link": "https://example.com"}])

    edit(cfg, "me", fill_me)
    edit(cfg, "offer", fill_offer)
    assert config_check.main(["--config-dir", str(cfg), "--strict"]) == 0


def test_sending_must_stay_human_only(cfg):
    edit(cfg, "policy", lambda d: d.update(sending="auto"))
    report = config_check.run_checks(cfg)
    assert any("human_only" in e for e in report.errors)


@pytest.mark.parametrize("value", [0, -1, None, "ten"])
def test_cap_must_be_positive_number(cfg, value):
    edit(cfg, "policy", lambda d: d["caps"].update(max_drafts_per_day=value))
    report = config_check.run_checks(cfg)
    assert any("caps.max_drafts_per_day" in e for e in report.errors)


def test_missing_word_limit_for_channel(cfg):
    edit(cfg, "policy", lambda d: d["word_limits"].pop("email"))
    report = config_check.run_checks(cfg)
    assert any("word_limits.email" in e for e in report.errors)


def test_broken_yaml_gives_clear_error(cfg):
    (cfg / "cadence.yaml").write_text("followup_days: [3, 7\n", encoding="utf-8")
    report = config_check.run_checks(cfg)
    assert any("cadence.yaml: YAML is broken" in e for e in report.errors)


def test_missing_file(cfg):
    (cfg / "sources.yaml").unlink()
    report = config_check.run_checks(cfg)
    assert any("sources.yaml: file is missing" in e for e in report.errors)


def test_me_name_required(cfg):
    edit(cfg, "me", lambda d: d.update(name=""))
    report = config_check.run_checks(cfg)
    assert any("'name' is empty" in e for e in report.errors)


def test_pattern_needs_keywords(cfg):
    edit(cfg, "problems", lambda d: d["patterns"][0].update(keywords=[]))
    report = config_check.run_checks(cfg)
    assert any("patterns[0].keywords is empty" in e for e in report.errors)


def test_pattern_offer_id_must_exist(cfg):
    edit(cfg, "problems", lambda d: d["patterns"][0].update(offer_id="offer-99"))
    report = config_check.run_checks(cfg)
    assert any("offer-99" in e for e in report.errors)


def test_followup_days_must_be_in_order(cfg):
    edit(cfg, "cadence", lambda d: d.update(followup_days=[7, 3]))
    report = config_check.run_checks(cfg)
    assert any("small to big" in e for e in report.errors)


def test_stage6_timer_settings_must_be_positive(cfg):
    edit(cfg, "cadence", lambda d: d.update(stale_after_days=0, no_response_after_business_days="ten"))
    report = config_check.run_checks(cfg)
    assert any("'stale_after_days' must be" in e for e in report.errors)
    assert any("'no_response_after_business_days' must be" in e for e in report.errors)
    edit(cfg, "cadence", lambda d: [d.pop(k) for k in ("stale_after_days", "no_response_after_business_days")])
    assert not config_check.run_checks(cfg).errors          # missing = default, fine


def test_fixed_price_warns_when_zero(cfg):
    edit(cfg, "offer", lambda d: d["offers"][0].update(pricing="fixed", price_usd=0))
    report = config_check.run_checks(cfg)
    assert any("price_usd" in w for w in report.warnings)
