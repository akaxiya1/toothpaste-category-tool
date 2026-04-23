from expense_tracker.modules import config_loader


def test_feature_helper_reads_booleans_and_dicts():
    cfg = {
        "features": {
            "desensitize": True,
            "time_decay": {"enabled": True, "half_life_days": 30},
            "subscription_calendar": {"enabled": False, "cadence": "monthly"},
        }
    }
    assert config_loader.feature(cfg, "desensitize") is True
    assert isinstance(config_loader.feature(cfg, "time_decay"), dict)
    assert config_loader.feature(cfg, "subscription_calendar") is False
    assert config_loader.feature(cfg, "missing", default=42) == 42


def test_load_returns_empty_when_no_file(tmp_path):
    assert config_loader.load(tmp_path / "does-not-exist.yaml") == {}


def test_load_json_fallback(tmp_path):
    json_path = tmp_path / "config.json"
    json_path.write_text('{"features": {"desensitize": true}}', encoding="utf-8")
    # pass the .yaml sibling so YAML path fails and JSON fallback triggers
    loaded = config_loader.load(tmp_path / "config.yaml")
    assert loaded == {"features": {"desensitize": True}}
