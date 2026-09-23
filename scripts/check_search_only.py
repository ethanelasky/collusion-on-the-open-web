"""Verify actual search-only inputs before launching the 50-per-model batch."""
from dataclasses import asdict, replace
import json
from pathlib import Path
import sys

from ai_collusion.arms import load_arms
from ai_collusion.preparation import prepare_episode
from ai_collusion.runner import load_models
from ai_collusion.wiki import Dump, load_spec, reconstruct


def main():
    original_root = Path(sys.argv[1])
    models = {m.name: m for m in load_models("experiments/kimi-native-tools.yaml")}
    arm = load_arms("experiments/working-search-only.yaml")[0]
    dump = Dump("data/collusion-wiki")
    spec = load_spec("wikitasks/sector61_state_seven.yaml")
    cut = reconstruct(dump, "dse/DataUSAStateSequenceCollab2027", 4)
    search = prepare_episode(spec, dump, cut, arm.variant, arm.mode, models["env-gpt-5.6"], 0,
        num_live_problems=7, wiki_write_instructions=True, hint=arm.hint)
    preview = prepare_episode(spec, dump, cut, arm.variant, arm.mode, models["env-gpt-5.6"], 0,
        num_live_problems=7, wiki_write_instructions=True, hint="search_result_and_wiki_preview")
    calls = [m["content"] for m in search.context["messages"] if m["role"] == "assistant"]
    text = "\n".join(m["content"] for m in search.context["messages"])
    assert sum("google.com/search" in c for c in calls) == 1
    assert not any("wikiservice.at" in c for c in calls)
    assert "--- GET https://wikiservice.at/" not in text
    assert "To edit this page using GET" not in text
    assert "DataUSA state sequence collaboration" in text
    assert "West Virginia" not in search.context["messages"][-1]["content"]
    assert search.context["messages"] == preview.context["messages"][:-2]
    assert search.world.ep.upcoming == preview.world.ep.upcoming
    assert search.world.task_clock() == preview.world.task_clock()
    original = next((original_root / "runs/hinted-write-on-100-20260909-gpt-5.6").glob("*env-neutral*.json"))
    old = json.loads(original.read_text())
    assert search.context["system"] == old["context"]["system"]
    assert search.context["messages"][:-2] == old["context"]["messages"][:-2]
    assert asdict(models["env-gpt-5.6"]) == old["env_model_config"]
    for model in ("gpt-5.6", "qwen3.8-27b", "glm-5.3"):
        old_path = next((original_root / f"runs/hinted-write-on-100-20260909-{model}").glob("*env-neutral*.json"))
        assert asdict(replace(models[model], max_tokens=4096)) == json.loads(old_path.read_text())["model_config"]
    kimi_path = next((original_root / "runs/working-write-on-native-tools-repair-20260910-kimi-k3").glob("*env-neutral*.json"))
    assert asdict(models["kimi-k3"]) == json.loads(kimi_path.read_text())["model_config"]
    print(json.dumps({"verified": True, "arm": arm.to_dict(), "models": list(models),
        "samples_per_model": 50, "seeds": "0..49", "prefill_messages": len(search.context["messages"]),
        "prefilled_wiki_reads": 0, "prefilled_searches": 1, "context_sha256": search.context_sha256,
        "same_completed_history_and_system_as_historical": True,
        "same_schedule_and_clock_as_current_preview": True,
        "context": search.context}, indent=2))


if __name__ == "__main__":
    main()
