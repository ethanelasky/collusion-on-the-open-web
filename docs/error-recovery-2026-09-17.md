# Recover model/environment errors in the cooldown grid

The user requested continuation of interrupted transcripts, then clarified that
the original 100-turn limit is a legitimate task outcome. Recovery therefore
targets **31 episodes: 22 model errors and 9 environment errors**. The 38 original
episodes ending at the turn limit are retained as completed observations.

## Method and comparability

The controller in [recover.py](../data/error-recovery-20260917/recover.py) uses the
original frozen episode loop, World router, deterministic environment and Sol
simulator from `data/run-snapshots/cooldown-grid-4models-20260915`. It adds an external
replay/checkpoint adapter; no frozen or current runtime source is modified.

Saved model and simulator generations reconstruct the world without making API
requests. Every accepted turn must reproduce its exact action, result, clocks,
effects, generation attempts and simulator prompt. The restored model history
must also match. For model errors, the next model call resumes. For environment
errors, the already-issued native action is retained and only its unaccepted
simulator reply is retried. Restoring the state before that action prevents a
failed retry from charging time or applying effects twice.

Each new accepted turn is checkpointed separately. Pending model actions are
saved before simulator requests, so an interruption does not require resampling
an already accepted action. Failed recovery passes are retained. Final derivative
transcripts link back to the original source SHA-256; all 400 originals and their
classifications remain unchanged.

The **total episode limit remains 100 turns**, including the original prefix.
Recovery retries transport/protocol failures, not wrong answers or missed rounds.
The existing local tool validator, simulator bookkeeping validator, shared
50-request limit, retry backoff, and fatal HTTP-401 stop remain active.

The three affected Qwen episodes continue with the already authorized, tested
Alibaba profile (`qwen3.8-27b`, thinking enabled, thinking budget 16,384, native
tools and automatic tool choice). Their retained prefixes were on OpenRouter.
The exact continuation model configuration and provider transition are recorded;
these are mixed-provider continuations and must remain identifiable in analysis.
The simulator and other model settings retain their original configuration,
except for the later DeepSeek output-budget override described below.

Retrying infrastructure errors changes the available observation length and
selects later successful provider generations. This is a recovery analysis,
separate from the original 400-record snapshot; it is not a new independent sample.

When eight ordinary recovery passes exhaust their retries, a supplemental
[feedback controller](../data/error-recovery-20260917/feedback_recovery.py) can explain
the same rejected-output rule more explicitly to the simulator. Alternate data
URL hostnames/paths do not receive admission in the frozen ledger, so they cannot
establish delivery of unseen target data. The explanation preserves the exact
requested action and leaves the router, admission policy, and validator unchanged.
Its text and actual request hash are saved with each affected simulator generation;
it applies only to the failing turn and is recorded in continuation provenance.
Three additional tests check this scope and request logging.

## Shell fact-extraction recovery

At the 20:59 UTC status check, 29 continuations had finished and all 29 had valid
new judgments. Two DeepSeek continuations had stopped after accepted turns 87
and 85. Both commands searched local HTML using a regex such as
`https://[a-zA-Z0-9...]`. The static fact extractor mistook the regex for an IPv6
URL and raised `ValueError: Invalid IPv6 URL` before reaching the simulator.
The secondary recovery controller then raised `IndexError` because this crash
had not produced an ordinary failed-attempt record.

The [scoped URL guard](../data/error-recovery-20260917/url_fact_recovery.py) skips
unparseable URL candidates only during shell fact extraction. The simulator
receives the exact original command and handles its local-file regex normally.
Valid URL candidates, request admissions, tool schemas, and task clocks retain
their original behavior. This compatibility fix is applied only to the last two
continuations, is recorded in their provenance, and does not edit frozen runtime
files or previously completed records.

Three regression tests reproduce the original crashes, verify that commands and
world state are unchanged by fact collection, and preserve valid URL candidates.
The [offline preflight](../data/error-recovery-20260917/url-preflight.json) replays
both complete saved prefixes, including all continuation checkpoints, and reaches
their pending simulator calls without API requests or changes to saved turns.

## DeepSeek output-budget override

At the user's request on September 17, the remaining DeepSeek controller was
restarted from accepted checkpoints with **100,000 output tokens for every new
generation**. Working seed 9 had already finished before the switch. Slow seed
49 used the new budget on turns 99 and 100; both requests succeeded through
OpenRouter/DeepInfra and produced native tool calls without a protocol retry.
The [live verification](../data/error-recovery-20260917/high-budget-verification.json)
records their source hashes, attempt numbers, providers, and token usage.
This replaces the 4,096-token initial allowance and applies from the first
attempt of each new turn. The existing DeepSeek API's output-token budget
includes reasoning and the final tool call. Pending accepted model responses
are reused, and historical/checkpoint generations keep their recorded budgets.

The [budget controller](../data/error-recovery-20260917/high_budget_recovery.py)
records the override in each derivative's continuation metadata; individual
generation attempts retain the exact requested configuration. This changes the
generation budget within slow seed 49, making it a mixed-budget continuation.
Its results cannot isolate the effect of a larger reasoning allowance.
The total episode limit remains 100 turns. The future OpenRouter DeepSeek
recovery profile also starts at 100,000 tokens with the same retry ceiling.

Fourteen offline recovery tests passed, including three budget-specific tests
covering initial requests, retries, subsequent turns, exact prefix preservation,
checkpoint reload, and the unchanged episode turn limit. A separate profile
test checks the future DeepSeek budget and preserves Qwen's existing profile.

## Validation and artifacts

Offline replay verifies all 31 accepted prefixes before live requests are allowed.
[Recovery tests](../data/error-recovery-20260917/test_recover.py) check exact replay,
tamper rejection, retrying the same pending native action, no double clock charge,
checkpoint reload, the unchanged 100-turn limit, selection of errors only, and the
verified Alibaba thinking/tool profile.

- [Replay preflight](../data/error-recovery-20260917/preflight.json)
- [Live progress](../data/error-recovery-20260917/progress.json)
- Derivative transcripts: `runs/cooldown-grid-4models-20260915-error-recovery-20260917/`
- Accepted turns and recovery attempts: `data/error-recovery-20260917/episodes/`

The classifier report now treats only model/environment errors as incomplete
transcripts. Its sensitivity analysis excludes those errors and retains other
task outcomes. The original resolved-only data remain in the supporting exports
for reproducibility.

## Completed recovery

All 31 continuations are saved and have valid new classifications. The current sample contains 400 source-verified judgments: 369 unchanged original observations and 31 continued transcripts. The original 400 records remain byte-identical.

See the [updated classifier report](cooldown-grid-recovered-classifier-results-2026-09-17.md) and [verification manifest](../data/error-recovery-20260917/classification-verification.json).
