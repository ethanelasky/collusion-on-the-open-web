"""Offline verification, executed with the frozen rollout package on sys.path."""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys

from ai_collusion.arms import load_arms
from ai_collusion.preparation import prepare_episode
from ai_collusion.runner import load_models
from ai_collusion.wiki import Dump, load_spec, reconstruct


def main():
    root = Path(sys.argv[1]).resolve()
    snapshot = Path.cwd()
    models = load_models("experiments/hinted-models.yaml")
    configs = {m.name: m for m in models}
    arm = load_arms("experiments/hinted-write-instructions.yaml", only=["working-write-on"])[0]
    dump = Dump("data/collusion-wiki")
    spec = load_spec("wikitasks/sector61_state_seven.yaml")
    cut = reconstruct(dump, "dse/DataUSAStateSequenceCollab2027", 4)
    results = []
    for model in ["gpt-5.6", "qwen3.8-27b", "glm-5.3", "kimi-k3"]:
        original = root / "runs" / f"hinted-write-on-100-20260909-{model}"
        manifest = json.loads((original / "manifest.json").read_text())
        identity = manifest["experiment"]
        for filename, wanted in identity["implementation"].items():
            assert hashlib.sha256((snapshot / "ai_collusion" / filename).read_bytes()).hexdigest() == wanted, filename
        for filename, wanted in identity["environment_prompts"].items():
            assert hashlib.sha256((snapshot / "ai_collusion" / filename).read_bytes()).hexdigest() == wanted, filename
        assert hashlib.sha256((snapshot / manifest["spec"]).read_bytes()).hexdigest() == identity["spec_file_sha256"]
        for filename, wanted in identity["dump"]["files"].items():
            assert hashlib.sha256((snapshot / "data/collusion-wiki" / filename).read_bytes()).hexdigest() == wanted
        assert asdict(configs[model]) == manifest["models"][0]
        assert asdict(configs["env-gpt-5.6"]) == manifest["env_model"]
        condition = next(c for c in identity["conditions"] if c["arm"]["id"] == arm.id)
        assert arm.to_dict() == condition["arm"]
        originals = [json.loads(p.read_text()) for p in original.glob("*env-neutral*.json")]
        assert len(originals) == 5 and {r["seed"] for r in originals} == set(range(5))
        for seed in [5, 49]:
            prepared = prepare_episode(spec, dump, cut, arm.variant, arm.mode, configs["env-gpt-5.6"], seed,
                num_live_problems=arm.num_live_problems, wiki_write_instructions=arm.wiki_write_instructions)
            assert prepared.context_sha256 == condition["context_sha256"]
            assert {**prepared.resolved_config, "max_turns": 100} == condition["resolved_config"]
            assert all(prepared.context == r["context"] for r in originals)
        results.append({"model": model, "existing": 5, "additional": 45, "target": 50,
                        "seeds": "5..49", "context_sha256": condition["context_sha256"]})
    print(json.dumps({"verified": True, "rollout_revision": "9217583", "arm": arm.to_dict(),
                      "models": results}, indent=2))


if __name__ == "__main__":
    main()
