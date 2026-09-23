# Color game campaigns

Use the notebook to edit prompts and inspect a few trajectories. Use the CLI
below for repeated runs. The supervisor runs in a separate process, so closing
Jupyter or the terminal does not stop the campaign. The game rules and prompts
are the same in both paths.

Run these commands from the repository with its Python environment active.
Set `OPENAI_API_KEY` in the shell or the repository `.env`. Use `--env-file`
to select another file. The default model is `gpt-5.6-luna-high`, configured in
[models.yaml](models.yaml), with the OpenAI Responses API and high reasoning.

## Launch 50 rollouts per setting

```bash
python -m experiments.color_game.cli launch \
  --out reports/color-game/luna-3x50 \
  --rollouts 50 \
  --workers 50 \
  --rounds 5 \
  --actions-per-agent 8
```

The output directory must be new. This starts 150 rollouts: 50 each for
`guessing_only`, `async_counter`, and `sync_counter`. There are 750 rounds in
total. Each player has at most eight actions per round, including the final
choice: at most 80 actions per rollout. Choosing a color ends that player's
turn early. Sync uses one shared 180-second deadline per round, including both
final answers.

Use `--settings async_counter` to run only the sequential counter setting.
For example, `--settings async_counter --rollouts 600` plans 600 rollouts and
3,000 rounds when `--rounds 5`. You can select more than one setting by listing
their names after `--settings`; repeated names are rejected. The default remains
all three settings. The launch receipt and campaign manifest save the selection,
and resume checks it before any model calls.

`--workers 50` limits active rollouts across the selected settings. Jobs enter the
queue in setting order for each repetition. Alice and Bob run in sequence in
guessing and async; sync can make two model requests at once. The worker limit
is therefore a rollout limit, not an API request limit.

Each rollout has fresh agents, its own counter store, and a random namespace.
Assigned colors are uniform and can repeat. The target colors and Bob's fuzz
tags are paired across settings for the same seed and rollout index. The default
seed is 17; `--start-index` defaults to zero. Neither the namespace nor the counter
store is shared between rollouts.

The launcher saves the configuration and a source copy with SHA-256 checksums.
The supervisor runs that copy and saves every rollout plan and system prompt
before model calls start. On macOS, it also uses `caffeinate` to prevent idle
sleep while the supervisor runs. `--no-caffeinate` disables this. Use
`--foreground` if the launch command should wait for the supervisor to finish.

## Inspect, stop, and recover

```bash
python -m experiments.color_game.cli status --out reports/color-game/luna-3x50
python -m experiments.color_game.cli stop --out reports/color-game/luna-3x50
python -m experiments.color_game.cli resume --out reports/color-game/luna-3x50
```

`status` reports whether the supervisor is active, plus counts and scores for
each setting. `stop` writes a stop request. Queued jobs stay queued; active jobs
stop at their next saved game event. A model request already in progress can
take time to return.

After a stop or computer restart, `resume` checks the saved source, inputs,
plans, and prompts. It starts only queued jobs whose output directories are
empty. It retains completed results, recovers completed files that the manager
had not yet recorded, and marks partial jobs as interrupted. It does not replay
partial or failed rollouts. The campaign manifest records each recovery. Thus,
a campaign with interrupted jobs can finish with fewer than 50 complete
rollouts per setting. A lock prevents two supervisors from running the same
campaign.

## Saved results

For the example above, open
`reports/color-game/luna-3x50/runs/index.html` in a browser. The page links each
saved transcript and refreshes every five seconds while the campaign runs.
The supervisor updates the report as progress events arrive. The report uses
the small campaign manifest and does not load all raw transcripts into memory.

| Path under the output directory | Contents |
|---|---|
| `request.json`, `launch.json` | Model and game settings, source checksums, and launch receipt |
| `source/`, `source-sha256.json` | Source copy used by the supervisor and file checksums |
| `process.json`, `worker.json`, `supervisor.log` | Process receipt, supervisor status, and progress log |
| `runs/campaign.json` | All planned jobs, current status, summaries, artifact paths, and recovery records |
| `runs/index.html`, `runs/progress.json`, `runs/rollouts.csv` | Browser report and compact exports |
| `runs/plans/` | Private assignments, namespace, complete system prompts, and prompt hashes for each rollout |
| `runs/<job>/rollout.json`, `events.jsonl`, `transcript.html` | Saved trajectory, event journal, and readable transcript |

Scores remain labelled partial while rounds are unfinished or invalid. Valid
accuracy uses completed rounds that the game marked valid. The report also
shows matches against the planned number of rounds, API and infrastructure
errors, and missing final choices. Live request counts are observed progress;
settled counts come from saved action records. A request count is a journaled
model attempt and does not include internal HTTP retries. Missing provider cost
data is not treated as zero.

Publish the saved transcripts with `python -m experiments.color_game.docent_sync
--campaign <output>/runs --state <output>/docent-sync.json --env-file .env --watch`.
This creates a public Docent collection and adds rollouts as they finish. Each
rollout keeps separate Alice and Bob histories, reasoning, errors, outcomes,
and source metadata. Pending provider replies settle before upload. See the
[Docent export details](../../docs/color-game.md) for comparison batches,
upload receipts, and watcher limits.

The [September 12 public transcript collections](../../docs/color-game/color-game-docent-2026-09-12.md)
cover Luna and the six-model comparison.

Plot accuracy for each round, pooled across rollouts, with:

```bash
python scripts/plot_color_round_accuracy.py \
  --comparison reports/color-game/<comparison>/comparison.json \
  --campaign reports/color-game/<earlier-luna-campaign>/runs \
  --out reports/color-game/<comparison>/figures
```

The script requires `matplotlib` and uses only completed campaigns. It exports
PNG, PDF, SVG, pooled counts in CSV, and source file checksums. Every round uses
all planned rollouts; missing final answers count as incorrect. It checks the
saved choices and campaign totals before drawing each model's round accuracy.

See [the game design](../../docs/color-game.md) for tool permissions and prompt
details. The [notebook](../../notebooks/color_game.ipynb) remains available for
prompt changes and single-rollout inspection.

## Requested model comparison

[models.comparison.yaml](models.comparison.yaml) contains GLM 5.3, DeepSeek V4
Flash 0731, Gemini 3.8 Flash, Claude Haiku 4.5, GPT-6 Astra, and GPT-5.6 Sol.
Select it with `--models experiments/color_game/models.comparison.yaml` and use
the model's `name` as `--model`. Each launch still runs 50 rollouts per setting.

The September 12 comparison initially used 8 workers for each OpenRouter model and
9 for each direct OpenAI model. GLM and DeepSeek were moved to replacement campaigns
with 2 workers each after provider rate limits; the final limit is 38 active rollouts.
All have the same colors, targets, action limits, and clock rules. GLM,
DeepSeek, Gemini, Astra, and Sol use high reasoning. Haiku uses a 26,214-token
thinking budget with automatic tool choice. Each response must still contain
exactly one valid action.

Provider checks are saved with the launch artifacts. GLM and DeepSeek use Inceptron FP4, whose endpoints support disabling parallel
tool calls. Friendli was excluded for GLM after broader tests still returned
multiple calls despite the parallel-call flag. The initial GLM and DeepSeek campaigns
were stopped after provider rate limits; their records are retained separately
from the replacement campaigns. Haiku uses OpenRouter's Anthropic endpoint. Astra and Sol use the direct
Responses API. Provider choice, precision, reasoning controls, and model
latency are limits on a comparison between models; they are recorded in the
configuration and raw responses.

The earlier Luna campaign used adapter v3. New campaigns use the corrected
action interface described in [the game design](../../docs/color-game.md).
Keep that interface version difference explicit when comparing scores.
