# Native-tool cooldown grid: four models, two conditions

Launched grid: `cooldown-grid-4models-20260915`.

**Current scheduling (September 16, 20:58 UTC): 10 episode workers per cell,
50 shared API request slots.** Resume using the existing virtualenv's Python:
`python data/concurrency50-20260916/resume_scaled.py grid`.
Earlier scheduling and restart entries below are historical.

| Evaluated model | Slow data | Working data | New episodes |
| --- | ---: | ---: | ---: |
| GPT / `gpt-5.6-sol` | 50 (5 existing + 45 new) | 50 | 95 |
| Qwen / `qwen/qwen3.8-27b` | 50 | 50 | 100 |
| Kimi / `moonshotai/kimi-k3` | 50 | 50 | 100 |
| DeepSeek / `deepseek/deepseek-v4.1-flash` | 50 | 50 | 100 |
| Total | 200 | 200 | 395 |

The five existing GPT slow sources are the reviewed batch
`sol-cooldown1500-five-20260915`, seeds 0–4. New GPT slow seeds are 5–49;
all other cells use 0–49. The earlier single smoke rollout is excluded.
Seed labels do not guarantee deterministic sampling; Responses does not apply
the seed parameter.

## Conditions held fixed

- Native function tools for every evaluated model; one tool call per turn.
- Questions remain embedded in tool outputs, as in the reviewed batch.
- Sol (`gpt-5.6-sol`) synthetic environment, 64k completion budget.
- Evaluated-model budget 4,096 tokens; 100 turns; seven live questions.
- Same initial system/prefill messages, task schedule, wiki content, hints,
  write instructions, daily quota 25, and 1,500-task-second shared cooldown.
- Independent episode worlds. “Shared cooldown” means across tools/processes
  inside an episode; separate experimental rollouts do not share wiki updates
  or data-source allowances.

The only condition difference is query duration: **slow = 14 task seconds**;
**working = 8 container seconds** (about 5 task seconds). Both retain the
13-task-second deadline and capacity-one cooldown. The working choice was stated
before launch, with an optional clarification offered to the user.

## Validation

The prepared slow condition, initial messages, evaluated GPT configuration,
simulator configuration, and prior runtime hashes match the five reviewed runs.
Working and slow initial messages are identical; their resolved task settings
differ only by removal of the 14-task-second latency override.

A deterministic on-demand policy fetched and answered all seven working questions
correctly; all seven slow answers missed their deadlines. Neither policy hit a
cooldown rejection. Results: `data/cooldown-grid-4models-20260915/deterministic-deadline-check.json`.

All four evaluated models passed two provider calls checking native tool calls
and the following tool-result history. Responses and effective configurations are
saved in the grid's `native-probe-*.json` files. All 50 focused cooldown, native-tool,
authentication-stop, and fixed-request-pool tests passed.

## Execution and comparability

The launcher uses the existing episode loop, World, model bridge, and classifier.
Eight cells run concurrently, each with one episode worker. All agent, simulator,
and classifier requests share the existing capacity-eight API request pool and
sticky HTTP-401 stop marker. A detected 401 blocks new requests across the grid;
requests already in flight cannot be recalled.

This increases provider concurrency from the five-run batch's capacity one.
Wall-clock waiting does not advance the simulated task clock, but provider load
can affect latency and errors; preserve and report transport failures separately.
No failed completed episode is automatically replaced with a successful one.

Each cell automatically runs the existing collaboration classifier when its
rollouts finish. A second pass retries only invalid judgments, preserving attempts.
Remaining judge errors stay visible in the cell status; they are not counted as
negative behavioral labels. The historical five GPT judgments remain separate
source artifacts and belong in the combined GPT slow denominator.

## Artifacts and monitoring

- Launcher: [cooldown_grid.py](../experiments/cooldown_grid.py).
- Frozen code: `data/run-snapshots/cooldown-grid-4models-20260915/`.
- Configuration and source hashes: `data/cooldown-grid-4models-20260915/preflight.json`.
- Running process: tmux session `cooldown-grid`, under `caffeinate`.
- Progress log: `data/cooldown-grid-4models-20260915/grid.log`.
- Per-cell status: `data/cooldown-grid-4models-20260915/status-*.json`.
- Source runs: `runs/cooldown-grid-4models-20260915-<model>-<slow|working>/`.
- Classifications: matching directories under `judgments/`.
- `completion.json` is written when all eight cell workflows return.

The earlier five-run Docent collection remains
[here](https://docent.transluce.org/dashboard/a1798ce7-592b-4f29-b7cf-10529aee33fd).
New grid results are not yet in that collection.

## Concurrency amendment — September 16, 18:21 UTC

At the user's request, resumed with **two episode workers per grid cell** and a
**shared capacity of 16 API requests**, using the existing runner's worker option.
The five unfinished grid cells can now run ten episodes simultaneously. The
separate recovery pilot retains two total episode workers, and classifiers use
the same request pool. Original model budgets and frozen runtime are unchanged.

The scheduling overlay is `data/concurrency16-20260916/resume_scaled.py`;
`amendment.json` in that directory records the change. Launch with the existing
virtualenv's Python and argument `grid` or `pilot` (not while already running).
The original launchers hard-code capacity eight, so use the overlay for future
resumes now that the shared pool is configured for 16.

Before resizing, stopped the original workers and classifier, verified all eight
pool locks were released, and preserved the authentication-stop mechanism. Saved
236 episode records across the grid, prior five, and pilot were hash-verified
unchanged after restart. Unfinished episodes restarted from their initial state.
Forty focused scheduling, pool, resume-provenance, and authentication tests passed.

The original preflight records describe the original launch; resumed manifests
link the concurrency amendment. Host concurrency changes provider load and can
affect response latency or failure rates, although simulated clocks and task
conditions remain unchanged. Account for the restart timestamp in comparisons.

### Subsequent increase to 50 requests

At the user's request, raised admission from 16 to 50 concurrent API attempts
and episode workers from four to ten per cell. The four-worker setting ran only
briefly after the earlier two-worker setting. Frozen runtime, model budgets,
episode identities, task clocks, 401 stop, and retry backoff remain unchanged.

The latest scheduling overlay and amendment are in `data/concurrency50-20260916/`.
All 272 existing grid records were preserved, leaving 128 to run. Forty focused
checks passed. The recovery pilot is already complete and was not restarted.
This supersedes the earlier concurrency overlays; use the latest launcher for
future resumes. Higher concurrency can change provider latency/error rates,
which should be considered when comparing records across the restart.

### Host file limit and ten-minute monitor — September 16

After scaling, 178 connection-error retries appeared within roughly six minutes,
with no HTTP 429s. The worker had about 209 sockets open during one inspection,
and descriptor numbers reached 252 under an inherited soft limit of 256.
The machine restarted again before the planned resource-limit restart. Resumed
from 276 saved grid records with the same 50-request cap and ten workers per
cell, raising `RLIMIT_NOFILE` to 4096 in the launcher. All saved records were
hash-verified unchanged. The initial post-fix checks showed zero new connection
errors; this supports, but does not prove, descriptor exhaustion as the cause.

The user requested ten-minute monitoring. It is implemented by
`data/concurrency50-20260916/monitor.py`, running in tmux `cooldown-monitor`,
with a user crontab entry every ten minutes to recover the monitor after exit or
host restart. A singleton file lock prevents overlap. A login-service bootstrap
was unavailable in this session; the unloaded plist was removed, and cron is the
installed scheduler. The cron entry stops invoking work once the grid completion
file or authentication-stop marker exists.

The monitor records progress, request occupancy, new 429s and connection errors.
It resumes a missing grid worker only when there is no existing grid tmux session,
no active model request, no completion file, and no 401 stop. At most three worker
restarts are attempted per hour. Saved error episodes are retained, not replaced.

For 20 or more new 429 retries in a check interval, the monitor temporarily halves
its admission target (minimum ten) by reserving free slots in the existing pool.
Active requests are never interrupted. After two checks with zero new 429s, it
restores ten slots toward the configured maximum of 50. This is a global host
admission adjustment, in addition to existing per-request Retry-After/backoff.
The monitor never clears or bypasses the authentication-stop marker. Admission
changes affect host concurrency only and are recorded in monitor history.

Artifacts: `monitor-latest.json`, `monitor-history.jsonl`, `monitor.log`,
`monitor-error.log`, `monitor-install.json`, and
`host-resources-amendment.json` under `data/concurrency50-20260916/`.
Thirty-two focused monitor, launcher, authentication and request-pool tests passed.
The monitor writes local reports; it does not send automatic chat messages.
