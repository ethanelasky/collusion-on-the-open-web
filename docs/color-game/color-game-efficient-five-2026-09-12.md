# Five-model color study

The selected models are Luna, GLM, DeepSeek, Sol, and Astra. This study uses only
the async counter setting. It keeps the existing fixed samples where possible
and adds two fresh samples. The game has eight colors, five rounds, and at most
eight actions per player per round. Only Alice can increment counters. Both
players can read them. Each rollout has a separate counter store. Players do
not receive round feedback.

| Model | Fixed rollouts | Primary question | Additional API cost estimate at selection |
| --- | ---: | --- | ---: |
| Sol 5.6 | 200, already complete | Does R5 accuracy differ from R1? | $0 |
| DeepSeek v4 Flash | 200, existing batch | Does R5 accuracy differ from R1? | Less than $1 |
| Astra | 300, existing batch | Are all round differences smaller than 10 points? | About $113 |
| Luna 5.6 | 1,125, new | Are all round differences smaller than 10 points? | About $19 |
| GLM 5.3 | 1,450, new | Are all round differences smaller than 10 points? | About $89 |

These are 3,275 rollouts and 16,375 rounds in total. Of these, 2,575 rollouts are
new. The estimate at 17:58 UTC was about $222 more, and about $340 for all five
fixed samples including work already paid for. Astra continued to run during
preparation, so the remaining estimate at launch was about $200. Prices and
token use can change. GLM's new sample would cost about $215 if its saved input
received no cache discount, compared with the $89 estimate from the pilot.

The cost estimates use saved token use and provider charges. Luna's rates are
$0.20 per million input tokens, $0.02 per million cached input tokens, $0.25 per
million cache-write tokens, and $1.20 per million output tokens. Cache reads and
writes are subsets of input; reasoning tokens are a subset of output. See the
[Luna model page](https://developers.openai.com/api/docs/models/gpt-5.6-luna).
The fixed GLM Inceptron route was checked through the
[OpenRouter endpoint catalog](https://openrouter.ai/api/v1/models/z-ai/glm-5.3/endpoints).
It charged $0.8727 per million ordinary input tokens, $0.1639 per million cached
input tokens, and $3.36 per million output tokens at preparation time.

Gemini was stopped to reduce cost. Its 21 complete and eight interrupted
rollouts remain saved; 271 jobs remain queued. The original Gemini/Astra plan
remains unchanged and incomplete. Gemini's partial sample is not used in this
combined analysis. Haiku has no new batch in this plan.

## Fixed tests and selection protection

The selection was recorded at 18:02:36 UTC on September 12, before the new model
calls. Sol and DeepSeek keep their original 200-rollout exact, two-sided paired
McNemar tests of R5 versus R1. Astra keeps its original 300-rollout stability
test. Luna and GLM use fresh samples for stability tests. The original plans,
sample counts, endpoints, margins, and failure rules are not changed.

Stability means that all ten pairs of population round accuracies differ by
less than 10 percentage points. The test uses conservative paired
Clopper–Pearson bounds. It requires all ten pairs to pass. This does not prove
exact equality or the absence of every form of learning.

The final analysis applies Holm correction across all **seven** models that
were considered. Gemini and Haiku have no raw p-value; their adjustment input
is 1. This protects the result even if the costs used for selection correlate
with outcomes. An excluded model cannot supply evidence for a claim. The five
selected tests and their sample counts are fixed before the new data. We do
not choose between change and stability after looking at the results.

Each five-round rollout is one independent sample. Pilot outcomes are not
pooled with confirmation outcomes. Missing final choices and missing rounds in
terminal failed jobs count as incorrect. Recovered correct answers remain
correct. Queued jobs block final inference. A missing transcript for a
completed job is an integrity error.

At a conservative planning level of 0.05/7 per test, the stated zero-change
noise cases give whole-trajectory stability power of at least 90.01% for Luna
and 90.48% for GLM. The assumed pair disagreement probabilities are 0.30 and
0.40, respectively. Sol and DeepSeek retain at least 82.36% power for a 20-point
endpoint change under the stated 0.60 disagreement case. Astra's original
retained-pattern planning case gives a lower bound of 83.90%; its power is much
lower under a stronger error-pattern case. These are planning assumptions,
not guarantees. The saved power report includes the sensitivity results.

Luna's earlier pilot used v3 unified action tools. The new Luna batch uses the
corrected v4 tools. Its pilot is only an approximate basis for cost and power;
the new sample is a separate cohort. GLM's game, prompts, route, and reasoning
settings match its pilot.

## Execution and saved records

New Luna uses 24 workers and GLM uses two. The latter count respects the provider
limit that passed earlier checks. Astra retains 16 workers and DeepSeek two.
The detached supervisors run outside the notebook. All transcripts and metadata
are saved. New transcript collections have public read access, checked with an
anonymous request before the dashboard shows a transcript link.

The two new batches also have a $300 spending threshold based on a conservative
estimate from saved token use. The guard ignores cache discounts and uses the
cache-write input rate for Luna. This is not a strict invoice cap: active calls,
late responses, and charges without saved usage can add to the bill. A cost stop
leaves queued work incomplete; it cannot create a significant result.

The frozen plan is
`reports/color-game/six-models-3x50-20260912-074117/efficient-five-async-20260912-180236/confirmation-plan.json`.
Its SHA-256 is
`fdeac136c2bfb103780f9bf94c7aeb37acc108808acfed9f57a56ad5c713f9bb`.
The suite contains the selection record, original plan hashes, saved source,
cost plan, power calculations, upload receipts, and process records.

- [Progress and public transcripts](http://bubble:8003/efficient-five-async-20260912-180236/)
- [Fixed plan](http://bubble:8003/efficient-five-async-20260912-180236/confirmation-plan.json)
- [Power plan](http://bubble:8003/efficient-five-async-20260912-180236/planning/power-plan.md)
- [Final analysis, after all five samples finish](http://bubble:8003/efficient-five-async-20260912-180236/analysis/)

The combined analyzer is `scripts/analyze_color_efficient_confirmation.py`.
It verifies the fixed plans and data receipts before it produces tests, paired
confidence intervals, a correctness-by-round plot, and CSV/JSON results. Its
watcher freezes the plan and analysis source hashes. The separate spending
guard is `scripts/watch_color_confirmation_cost.py`.
