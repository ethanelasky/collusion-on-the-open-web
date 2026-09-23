# Fresh confirmation of color accuracy changes

On 12 September 2026, the user authorized more samples after review of the
[pilot statistical analysis](color-game-statistics-2026-09-12.md). The new
batch has **200 fresh async-counter rollouts each for Sol and DeepSeek**:
400 rollouts and 2,000 rounds in total.

[Live progress and transcripts](http://bubble:8003/confirmation-async-2x200-20260912-165704/)
are served over Tailscale. The
[fixed plan](http://bubble:8003/confirmation-async-2x200-20260912-165704/confirmation-plan.json)
was saved before either supervisor started. Its SHA-256 is
`9379563a77740fd395cd1167eaa03a7afa0717c3271c9c3c998f9229bc3ca639`.

## Design and decision rule

Each rollout has the same eight colors, five rounds, and eight actions per
player per round as the pilot. Alice receives a private, uniformly assigned
color in each round. Colors can repeat. Only Alice can increment counters;
both players can read them. Agents, namespaces, and counter stores are fresh
for each rollout. The new seed is `1633944107`, with rollout indices 0–199.
Targets are paired across the two models by index. The pilot used seed 17.

The two primary tests compare round 5 accuracy with round 1 accuracy. They
use exact two-sided McNemar tests with Holm correction across the two models.
Only the fresh samples enter these tests. Positive change with adjusted
p ≤ .05 supports improvement; significant negative change supports decline.
Other results are inconclusive. These tests do not identify learning as the
cause of a change. Other round comparisons and slopes are exploratory.

The fixed sample has **91.72% power per test** for a 20 percentage point
change if the probability of changed correctness between rounds 1 and 5 is
0.60. Power for a 10-point change under that assumption is only 30.83%.
These assumptions and sample counts come from the saved exact power plan.
There is no guarantee of significance. A non-significant result does not
establish practical equivalence.

All 200 planned rollouts per model remain in the denominator. Missing final
choices and unrecorded rounds in terminal failed jobs count as incorrect.
Recovered API errors can still yield correct outcomes. Failed or partial
rollouts are not replaced. Queued jobs block the final analysis. The sample
size and stopping rule do not depend on accuracy or p-values.

## Execution and saved data

| Model | Saved route | Active rollout limit |
|---|---|---:|
| Sol | `gpt-5.6-sol`, direct Responses API, high reasoning | 24 |
| DeepSeek | `deepseek/deepseek-v4-flash-0731`, Inceptron, high reasoning | 2 |

DeepSeek retains its tested concurrency limit after provider rate errors in
the pilot. Provider routes, model parameters, retries, prompts, tool behavior,
and game code match the saved pilot. Source checksums confirm that only the
campaign selection, report, CLI, and Docent code changed. The supervisors
run saved source copies outside Jupyter and prevent idle sleep while active.

The CLI now accepts `--settings async_counter` to select this arm. Its default
still runs all three settings. It records the selection and verifies it on
resume. Each campaign retains plans, system prompts, source hashes, raw
responses, event journals, and readable transcripts.

The new public Docent collections are separate from the pilot collections:

- [Sol confirmation transcripts](https://docent.transluce.org/dashboard/87ca8a28-d323-4074-8b78-741a1e2e3b63).
- [DeepSeek confirmation transcripts](https://docent.transluce.org/dashboard/74a5a15a-4c4e-41f0-a1cc-850235e167dd).

Public read access was checked with an unauthenticated request. A watcher adds
completed transcripts as they become available and checks public transcript
access after the first upload. New collection names include the campaign ID
to prevent mixing fresh samples with a prior campaign that has the same name.

## Analysis and controls

The offline confirmation analyzer reads the fixed plan and waits for both
campaigns to reach a final state. It writes paired endpoint tests, per-round
confidence intervals, plots, and a report to the
[confirmation analysis page](http://bubble:8003/confirmation-async-2x200-20260912-165704/analysis/).
It makes no model calls and does not include the pilot in the primary tests.

The batch path is saved in `reports/color-game/latest-confirmation.txt`.
Each model directory supports the existing CLI `status`, `stop`, and `resume`
commands. Resume starts only queued work and retains completed or interrupted
records. The fixed plan is not changed during recovery.
