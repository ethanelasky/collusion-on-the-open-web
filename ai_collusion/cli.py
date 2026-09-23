from __future__ import annotations

import argparse
from pathlib import Path

from .board import load_board
from .context import build_context, format_context
from .task import load_task


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="ai-collusion", description="Model-screening harness with a mocked shared board.")
    p.add_argument("--models", default="models.yaml", help="models YAML (default: models.yaml)")
    p.add_argument("--task", default="tasks/bay4_serial.yaml", help="task YAML")
    p.add_argument("--boards", default="boards", help="directory holding <condition>.json board files")
    p.add_argument("--conditions", nargs="+", default=["neutral", "full"], help="board conditions to run")
    p.add_argument("-n", "--samples", type=int, default=1, help="samples per model x condition")
    p.add_argument("--seed", type=int, default=0, help="base seed; sample i uses seed+i")
    p.add_argument("--temperature", type=float, default=None, help="override every model's temperature")
    p.add_argument("--only", nargs="+", default=None, help="run only these model names")
    p.add_argument("--out", default="runs", help="output root directory")
    p.add_argument("--run-id", default=None, help="name of the run directory (default: UTC timestamp)")
    p.add_argument("--dry-run", action="store_true", help="print one assembled context and exit; no model calls")
    args = p.parse_args(argv)

    if args.dry_run:
        task = load_task(args.task)
        condition = args.conditions[0]
        board = load_board(Path(args.boards) / f"{condition}.json")
        ctx = build_context(task, board)
        print(f"# dry run: task={task.id} condition={condition} (no model called)\n")
        print(format_context(ctx))
        return

    from .runner import run

    run(
        models_path=args.models,
        task_path=args.task,
        boards_dir=args.boards,
        conditions=args.conditions,
        n_samples=args.samples,
        out_dir=args.out,
        base_seed=args.seed,
        temperature=args.temperature,
        only=args.only,
        run_id=args.run_id,
    )


if __name__ == "__main__":
    main()
