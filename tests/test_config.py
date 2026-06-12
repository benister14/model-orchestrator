from datetime import date

from orchestrator.config import effective_models, load_config


def test_config_loads():
    cfg = load_config()
    assert "lanes" in cfg and "models" in cfg


def test_sensitive_roles_stay_in_trusted_lane():
    # load_config() raises ConfigError if any *_sensitive role escapes the
    # trusted lane; a clean load means the invariant holds.
    load_config()


def test_eol_autofire():
    cfg = load_config()
    after = effective_models(cfg, today=date(2026, 8, 1))
    assert "deepseek-r1" not in after  # eol 2026-07-24
