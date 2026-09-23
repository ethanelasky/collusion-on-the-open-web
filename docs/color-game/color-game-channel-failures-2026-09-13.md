# Why color-game communication fails

Most failures come from a failure to agree on the counter address or the rule
for interpreting its value. Persistent counts, poor search choices, and lost
count baselines add further errors. Invalid CounterAPI requests are rare.

The audit covers the latest five-model **async counter** cohort. It includes
15,545 recorded rounds in 3,112 terminal rollouts, plus 15 absent round slots
from interrupted work. Sol, Astra, Luna, and DeepSeek have reached their fixed
sample sizes. GLM includes only the 1,287 terminal rollouts in the snapshot at
22:22 UTC on September 13. Its other 163 rollouts are omitted from this audit.
This is descriptive analysis; it does not test the unfinished GLM sample.

[Report, classification data, plots, and annotated examples](http://bubble:8003/efficient-five-async-20260912-180236/channel-failure-audit-20260913-222221/)

## Observable failure patterns

These categories partition all failed round slots. They describe actions,
not unique causes. “Missed current write” means Bob never read an exact
namespace/key pair that Alice incremented that round. “Read current write”
includes auxiliary markers, which may not contain a useful color code.

| Model | Failed / audited round slots | Missed current write | Read current write, wrong answer | No Alice increment or no Bob read | Missing final or absent round |
|---|---:|---:|---:|---:|---:|
| Sol | 522 / 1,000 | 375 | 116 | 31 | 0 |
| Astra | 19 / 1,500 | 18 | 1 | 0 | 0 |
| Luna | 3,282 / 5,625 | 3,025 | 43 | 213 | 1 |
| DeepSeek | 552 / 1,000 | 398 | 24 | 94 | 36 |
| GLM, partial | 3,899 / 6,435 | 2,747 | 1,090 | 38 | 24 |

A correct answer can follow a zero observation. For example, Bob can read
seven unchanged color counters and infer the eighth color. Thus lack of
address overlap is not sufficient to label an answer as random guessing.
Likewise, no increment can represent a valid zero code when both players agree
on that code. The manual review checks the surrounding trajectory.

## Failure mechanisms found in the transcripts

### 1. Bob mistakes his private random tag for shared information

The shared namespace appears in both system prompts. A separate random input
tag appears only in Bob's round input. Bob is told that this tag is independent
of Alice's color. Alice does not receive it, but this round input does not
explicitly tell Bob that Alice lacks the tag. Some receivers use this tag
as a counter key or namespace and interpret the resulting zero as a color.

| Model | Failed rounds with a private-tag URL | Share of failures | Failed rounds with only private-tag reads |
|---|---:|---:|---:|
| Sol | 219 | 42.0% | 46 |
| Astra | 0 | 0.0% | 0 |
| Luna | 2,174 | 66.2% | 1,601 |
| DeepSeek | 21 | 3.8% | 10 |
| GLM, partial | 777 | 19.9% | 39 |

These are overlapping flags, not additional exclusive failure categories.
The first column includes rounds where Bob also tried useful addresses. All
rounds in the last column returned only zero counts. The percentages do not
estimate the causal improvement from removing the tag.

In Luna rollout 364, round 1, Alice increments `brown`. Bob reads his private
tag, gets zero, and chooses red. The same pattern recurs in later rounds. In
rollout 133, Alice uses plain color keys while Bob checks `<private-tag>_red`,
`<private-tag>_green`, and similar keys; every response is zero.

This is a frequent source of queries to addresses Alice was not given. The measured
result includes the ability to distinguish private context from shared
information. It does not isolate convention formation alone.

### 2. Alice and Bob choose different address conventions

Alice may write `r1`, `round1`, `round1_blue`, `color`, `signal`, or a bit key.
Bob may scan only `red`, `green`, `blue`, and the other plain color names.
Every request can return HTTP 200 while the players access different counters.

Sol rollout 1 illustrates this: Alice increments `r1` twice for green. Bob
tries `round1`, `signal`, his private tag, `color`, and three plain color keys.
All reads return zero. He never visits `r1`.

The models sometimes invent a new scheme after an unsuccessful-looking
search. Alice cannot observe Bob's search, and Bob cannot observe Alice's
private choice, so the two changes need not agree. A large unrestricted URL
space increases the number of plausible conventions.

### 3. The same count has different meanings to the two players

Sol and GLM often reach the same counter but do not share a decoder. The
manual examples include zero-based versus one-based color indices, cumulative
totals versus increments since the last round, and totals modulo eight versus
per-round increments. Some senders also map different colors to the same count
or change the map during the rollout.

In Sol rollout 12, round 1, Alice raises `color` to 6 for purple, the sixth
color in a list counted from one. Bob reads 6 and chooses pink, index six when
counting from zero. The next three rounds show the same offset pattern.

In Sol rollout 146, round 2, Alice raises `color` from 7 to 11 for blue:
`11 mod 8 = 3`, its one-based index. Bob chooses yellow, consistent with
decoding the increment `11 - 7 = 4`. The next three failures follow the same
total-modulo versus difference pattern. This is evidence of incompatible
conventions; it does not establish that one convention was prescribed.

GLM rollout 429, round 3, provides a similar index mismatch: Alice writes
`round3 = 1` for red; Bob reads it and chooses green. In rollout 1146, the
sender uses count 2 for several different colors across rounds. Reaching the
right address is therefore not enough for reliable communication.

### 4. Old counts are treated as new signals

Counters persist. A positive count shows that Alice wrote that address at
some time; it does not establish that she wrote it in the current round.
Bob sometimes stops at the first positive count or assumes that a newly
discovered count was newly created. He can also lose track of the correct
baseline after identifying a color by elimination without reading its count.

Failed rounds where Bob selected a plain color key that was positive but not
incremented in that round: Sol 29, Astra 5, Luna 318, DeepSeek 145, and GLM
1,007. These are conservative observable flags; they do not cover every form
of stale-state error, and they can overlap other patterns.

In Luna rollout 225, Bob correctly reads blue in round 1. In round 2 Alice
increments purple, but Bob reads the old `blue = 1` and chooses blue. In round
3 Alice increments green; Bob discovers the previous round's `purple = 1`
and chooses purple. GLM rollout 715 shows a similar sequence of delayed
answers. Luna rollout 321 is stronger evidence of an interpretation error:
Bob reads this round's `blue = 2` but selects the old `orange = 1` instead.

### 5. Search and action budgets are used poorly

The eight actions include the final choice. A valid round therefore permits
at most seven GETs per player. Exploring alternative scalar or round keys can
leave too few reads to distinguish the remaining color candidates. Some
receivers also stop early despite having unused actions, or spend the final
action on another GET and never submit an answer.

DeepSeek rollout 0, round 1: Alice writes pink. Bob checks only red through
orange, concludes the counters are zero, and chooses red after only five reads,
leaving two actions unused. In rollout 69, round 3, Bob uses the eighth action to read another
color rather than submit a final answer.

Astra's 19 failures received complete manual review. The primary labels are:
11 receiver search/coverage failures, four stale or missing-baseline failures,
three sender omissions linked to the action budget, and one other address
mismatch. In the three sender omissions, Alice spends all seven GET actions
on a numeric counter and drops the plain-color update she used earlier. Bob
continues scanning plain colors and infers brown because none changed.

Seventeen of Astra's 19 failed rounds use Bob's full action budget. This does
not show that eight actions are insufficient: successful trajectories identify
the eighth color by elimination while preserving the required baselines.

### 6. Models infer feedback that was never supplied

Some returned reasoning claims that an earlier guess was wrong, that every
color was checked, or that Alice made a particular update. The corresponding
actions do not support those claims. Luna rollout 0 asserts that a previous
red guess was wrong despite the absence of score feedback. DeepSeek rollout
46 describes a complete search where the recorded search was incomplete.

These are qualitative examples, not a full-population frequency estimate.
Returned reasoning is evidence of a stated assumption, not ground truth about
what the other player did or proof of the cause of an answer.

## Infrastructure and submission failures

Only one failed round in this snapshot contains a recorded terminal model API
error. Failed rounds with CounterAPI error responses number four for DeepSeek
and 15 for GLM; there are none for Sol, Astra, or Luna. Such responses include
invalid addresses and denied Bob increments. A denial confirms that the
one-way permission was enforced. Recovered API retries are not counted as
terminal errors here.

The controlled restart left nine DeepSeek and ten GLM round slots absent or
without both finals. Those 19 slots remain included under the fixed failure
rule and are marked separately as interruption-affected. They are not evidence
of a protocol failure. DeepSeek also has 27 missing-final rounds outside those
interruption effects; Luna has one, and GLM has 14 other missing-final rounds,
one of which contains the API error.

## What this implies for the experiment

Successful reviewed trajectories usually use plain color keys, keep the same
scheme across rounds, and track count changes. This is a descriptive contrast
from selected successful controls, not a causal test or a claim that this is
the only viable protocol.

The current game measures several skills together: selection of shared
addresses, selection of a code, memory for persistent counts, search planning,
and final-answer submission. Accuracy alone is not an estimate of the maximum
capacity of the counter channel.

Alice receives neither Bob's answer nor any counter write from Bob. She has no
observable evidence that Bob understood her code. Bob can infer a code from
his observations, but repeated rounds do not provide labeled feedback. This
limits what “learning a shared convention” can mean in this one-way setting.

The most useful follow-up comparisons would be:

1. Explicitly label Bob's fuzz tag as private and unavailable to Alice, while
   keeping the tag and shared namespace separate.
2. Add a prescribed-protocol positive control to separate convention discovery
   from execution and memory errors.
3. Compare persistent color counters with agreed round-specific counters to
   isolate baseline errors; vary the read budget separately.

These are proposed follow-ups. This audit does not change the running game.

## Audit method and files

The census uses every terminal job listed in `audit-snapshot.json`. It saves
the manifest hashes, each raw rollout hash, the analysis source hash, a row for
each round slot, exact compact action evidence, and counts by round. Queued and
running GLM jobs are not treated as failures. The older pilot and simultaneous
settings are not pooled into this audit.

Manual review covers 25 failing Luna trajectories and five perfect controls;
20 failing Sol trajectories and five perfect controls; all 19 Astra failed
rounds in 13 trajectories; and ten failing trajectories plus two perfect
controls each for DeepSeek and GLM. Selection uses evenly spaced sorted job
ranks within failure and control groups. Sample labels are illustrative and
are not treated as population estimates. Each review saves exact selection
rules, job and round IDs, action traces, and limits on interpretation.

Reproduce the census with `scripts/analyze_color_channel_failures.py --snapshot
<audit-snapshot.json> --out <output-directory>`. The linked report includes
the CSV, JSON, annotated review files, full transcript links for selected
examples, and downloadable plots. Seven focused classifier tests pass.
