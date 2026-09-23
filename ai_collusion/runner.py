"""Run models x conditions x samples and write one JSON transcript per trial."""
from __future__ import annotations

import sys
import time
import traceback
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .board import load_board
from .client import ModelConfig, generate
from .context import build_context
from .task import load_task
from .run_storage import check_resume, completed_record, stable_sha256, write_json

REPO_ENV = Path(__file__).resolve().parents[1] / ".env"


def load_repo_env(path: Path = REPO_ENV) -> bool:
    """Load API keys from the repo's .env into os.environ (existing variables win).

    Returns True if the file was found and read. Missing file or missing python-dotenv is not an error:
    keys can still come from the shell environment.
    """
    if not path.is_file():
        return False
    try:
        from dotenv import load_dotenv
    except ImportError:
        return False
    load_dotenv(dotenv_path=path, override=False)
    return True


def load_models(path: str | Path, only: list[str] | None = None) -> list[ModelConfig]:
    load_repo_env()
    data = yaml.safe_load(Path(path).read_text())
    defaults = data.get("defaults", {}) or {}
    models = [ModelConfig.from_dict(m, defaults) for m in data["models"]]
    if only:
        wanted = set(only)
        models = [m for m in models if m.name in wanted]
        missing = wanted - {m.name for m in models}
        if missing:
            raise SystemExit(f"models not found in {path}: {sorted(missing)}")
    return models


def trial_filename(model: ModelConfig, condition: str, sample: int, seed: int) -> str:
    return f"{model.name}__{condition}__n{sample:02d}_seed{seed}.json"


def run(
    *,
    models_path: str,
    task_path: str,
    boards_dir: str,
    conditions: list[str],
    n_samples: int,
    out_dir: str,
    base_seed: int,
    temperature: float | None,
    only: list[str] | None,
    run_id: str | None = None,
) -> Path:
    """Mocked-board scenario: one task file x board conditions."""
    task = load_task(task_path)
    boards = {c: load_board(Path(boards_dir) / f"{c}.json") for c in conditions}
    contexts = {c: build_context(task, boards[c]) for c in conditions}
    return run_contexts(
        models=load_models(models_path, only),
        contexts=contexts,
        task_id=task.id,
        reference={"answer": task.answer, "notes": task.notes},
        manifest_extra={"task": task_path, "boards_dir": boards_dir},
        n_samples=n_samples, out_dir=out_dir, base_seed=base_seed, temperature=temperature, run_id=run_id,
    )


def run_contexts(
    *,
    models: list[ModelConfig],
    contexts: dict[str, dict],
    task_id: str,
    reference: dict,
    n_samples: int,
    out_dir: str,
    base_seed: int,
    temperature: float | None,
    run_id: str | None = None,
    manifest_extra: dict | None = None,
    reference_per_condition: bool = False,
) -> Path:
    """Generic loop: models x prebuilt {condition: {system, messages}} x samples -> runs/<id>/*.json.

    `reference` is written into every transcript; with reference_per_condition=True it is a
    {condition: reference} map and each transcript gets only its own entry.
    """
    if not models or type(n_samples) is not int or n_samples < 1 or not contexts:
        raise ValueError("runs require models, contexts, and a positive sample count")
    run_id = run_id or datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out = Path(out_dir) / run_id
    out.mkdir(parents=True, exist_ok=True)
    identity = {
        "schema_version": 1, "task_id": task_id, "contexts": contexts,
        "models": [asdict(m) for m in models], "reference": reference,
        "reference_per_condition": reference_per_condition,
        "base_seed": base_seed, "temperature_override": temperature,
        "metadata": manifest_extra or {},
    }
    experiment_sha256 = stable_sha256(identity)
    check_resume(out, experiment_sha256, n_samples)
    write_json(out / "manifest.json", {
        **(manifest_extra or {}),
        "run_id": run_id, "task_id": task_id, "conditions": list(contexts),
        "n_samples": n_samples, "base_seed": base_seed,
        "temperature_override": temperature, "models": [asdict(m) for m in models],
        "reference": reference, "experiment_sha256": experiment_sha256, "experiment": identity,
    })

    total = len(models) * len(contexts) * n_samples
    done = 0
    for model in models:
        temp = temperature if temperature is not None else model.temperature
        for condition, ctx in contexts.items():
            for i in range(n_samples):
                seed = base_seed + i
                done += 1
                fname = trial_filename(model, condition, i, seed)
                path = out / fname
                provenance = {"run_id": run_id, "experiment_sha256": experiment_sha256,
                              "condition": condition, "sample_index": i, "seed": seed}
                if completed_record(path, provenance):
                    print(f"[{done}/{total}] skip (exists) {fname}", file=sys.stderr)
                    continue
                print(f"[{done}/{total}] {model.name} {condition} n={i} seed={seed} ...", file=sys.stderr, end="", flush=True)
                t0 = time.monotonic()
                response, error = None, None
                try:
                    response = generate(model, ctx["system"], ctx["messages"], temperature=temp, seed=seed)
                except Exception as exc:  # noqa: BLE001 - record and keep going
                    error = {"type": type(exc).__name__, "message": str(exc), "traceback": traceback.format_exc()}
                dt = time.monotonic() - t0
                record = {
                    **provenance,
                    "model": {"name": model.name, "transport": model.transport, "model_id": model.model,
                              "base_url": model.base_url, "extra_body": model.extra_body,
                              "thinking": model.thinking, "effort": model.effort},
                    "task_id": task_id,
                    "seed_applied": bool(response and response.get("seed_applied")),
                    "temperature": temp,
                    "max_tokens": model.max_tokens,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "duration_s": round(dt, 2),
                    "context": ctx,
                    "response": (
                        {k: response[k] for k in ("text", "reasoning", "finish_reason", "usage", "raw")}
                        if response else None
                    ),
                    "reference": reference.get(condition) if reference_per_condition else reference,
                    "error": error,
                }
                write_json(path, record)
                status = "ERROR " + error["type"] if error else f"ok ({dt:.1f}s)"
                print(f" {status}", file=sys.stderr)
    print(f"wrote {out}", file=sys.stderr)
    return out
