import json

import pytest
import yaml

from ai_collusion.arms import Arm, load_arms, validate_arms
from ai_collusion.wiki_cli import main
from test_preparation import PAGE, ROOT, scenario


def write_arms(tmp_path, rows):
    path = tmp_path / "arms.yaml"
    path.write_text(yaml.safe_dump({"arms": [{"hint": "none", **row} for row in rows]}))
    return path


def test_loader_normalizes_and_filters_without_hiding_invalid_rows(tmp_path):
    rows = [dict(id="base", variant="base", mode="neutral"),
            dict(id="request", variant="notable_request", mode="evil", max_tokens=4096,
                 max_turns=2, num_live_problems=1)]
    path = write_arms(tmp_path, rows)
    arms = load_arms(path, only=["request", "base"])
    assert [a.id for a in arms] == ["base", "request"]
    assert arms[0].variant is None
    assert load_arms(path, only=["request"])[0].to_dict()["max_tokens"] == 4096
    with pytest.raises(ValueError):
        load_arms(path, only=["missing"])
    rows[0]["unknown"] = True
    path = write_arms(tmp_path, rows)
    with pytest.raises(ValueError):
        load_arms(path, only=["request"])


@pytest.mark.parametrize("change", [
    {"id": "../escape"}, {"mode": "unreliable"}, {"max_tokens": 0},
    {"max_tokens": True}, {"max_turns": 1.5}, {"max_turns": -1},
    {"num_live_problems": 0}, {"num_live_problems": True}, {"num_live_problems": "1"},
])
def test_invalid_arm_values(tmp_path, change):
    path = write_arms(tmp_path, [{**dict(id="a", variant="base", mode="neutral"), **change}])
    with pytest.raises(ValueError):
        load_arms(path)


def test_duplicate_and_unavailable_presets_fail(tmp_path):
    row = dict(id="a", variant="base", mode="neutral")
    with pytest.raises(ValueError):
        load_arms(write_arms(tmp_path, [row, row]))
    spec, dump, cuts = scenario(tmp_path)
    for arm in (Arm("a", "missing", "neutral"), Arm("a", None, "neutral", num_live_problems=3)):
        with pytest.raises(ValueError):
            validate_arms([arm], spec, cuts)


def test_cli_json_preview_filter_and_ambiguous_flags(tmp_path, capsys):
    spec, dump, cuts = scenario(tmp_path)
    path = write_arms(tmp_path, [dict(id="a", variant="base", mode="neutral"),
                                 dict(id="b", variant="notable", mode="evil")])
    args = ["--dump", str(dump.root), "preview", "--page", PAGE, "--rev", "4",
            "--spec", str(ROOT / "wikitasks/sector61_state.yaml"), "--arms", str(path),
            "--arm", "b", "--json"]
    main(args)
    previews = json.loads(capsys.readouterr().out)
    assert {p["arm_id"] for p in previews} == {"b"}
    assert {p["role"] for p in previews} == {"evaluee", "env-model"}
    assert all(len(p["context_sha256"]) == 64 for p in previews)
    for conflict in (["--variant", "base"], ["--mode", "neutral"], ["--hint", "none"]):
        with pytest.raises(SystemExit):
            main(args + conflict)
    with pytest.raises(SystemExit):
        main(["preview", "--page", PAGE, "--rev", "4", "--arm", "a"])
