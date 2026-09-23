import hashlib
import json

import pytest

from scripts.watch_color_confirmation_cost import check, estimate


def test_cache_and_reasoning_are_not_added_to_usage_twice():
    manifest = {"summary": {"cost": 999}, "outcomes": [{"summary": {"usage": {
        "prompt_tokens": 1_000_000, "completion_tokens": 1_000_000,
        "cached_tokens": 800_000, "reasoning_tokens": 900_000}}}]}
    assert estimate(manifest, .25, 1.2) == (1.45, 0)


def test_reported_charge_overrides_lower_token_estimate():
    assert estimate({"outcomes": [{"summary": {"cost": 7, "usage": {"prompt_tokens": 100}}}]}, 1, 1) == (7, 0)


def fixture(tmp_path, cost):
    directory = tmp_path / "new"
    (directory / "runs").mkdir(parents=True)
    launch = directory / "launch.json"
    launch.write_text("{}")
    (directory / "runs/campaign.json").write_text(json.dumps({"status": "running", "outcomes": [
        {"status": "complete", "summary": {"cost": cost}}]}))
    return {"threshold_usd": 10, "campaigns": [{"directory": str(directory),
            "launch_sha256": hashlib.sha256(launch.read_bytes()).hexdigest(),
            "input_per_million": .25, "output_per_million": 1.2}]}, directory


def test_crossing_budget_stops_only_named_campaign(tmp_path):
    config, directory = fixture(tmp_path, 10)
    unrelated = tmp_path / "old"
    unrelated.mkdir()
    result = check(config, stop=True)
    assert result["stop_reason"] == "spending_threshold"
    assert (directory / "stop-request.json").exists()
    assert not (unrelated / "stop-request.json").exists()
    assert json.loads((directory / "runs/campaign.json").read_text())["status"] == "running"


def test_no_stop_below_threshold_and_existing_stop_is_preserved(tmp_path):
    config, directory = fixture(tmp_path, 9)
    assert check(config, stop=True)["stop_reason"] is None
    target = directory / "stop-request.json"
    assert not target.exists()
    target.write_text("user stop")
    config["threshold_usd"] = 5
    check(config, stop=True)
    assert target.read_text() == "user stop"


def test_complete_missing_usage_is_not_treated_as_free(tmp_path):
    config, directory = fixture(tmp_path, None)
    assert check(config, stop=True)["stop_reason"] == "unmeasured_complete_rollouts"
    assert (directory / "stop-request.json").exists()


@pytest.mark.parametrize("value", [-1, float("nan"), float("inf"), True, "1"])
def test_invalid_usage_rejected(value):
    with pytest.raises(ValueError):
        estimate({"outcomes": [{"summary": {"usage": {"prompt_tokens": value}}}]}, 1, 1)


def test_launch_tamper_and_duplicate_paths_rejected(tmp_path):
    config, directory = fixture(tmp_path, 5)
    config["campaigns"].append(config["campaigns"][0])
    with pytest.raises(ValueError, match="Duplicate"):
        check(config)
    config["campaigns"].pop()
    (directory / "launch.json").write_text('{"changed":true}')
    with pytest.raises(ValueError, match="launch differs"):
        check(config)
