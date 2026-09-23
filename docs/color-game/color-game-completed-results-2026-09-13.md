# Completed color-game samples: September 13

Sol, Astra, and Luna have reached their fixed sample sizes. The user requested
their tests before DeepSeek and GLM finish. The completed-model release uses
Bonferroni correction across all seven original candidate models. It does not
test partial samples or change a sample size, primary test, or stability margin.

| Model | Rollouts | Round 1 | Round 5 | Corrected p | Fixed-test result |
| --- | ---: | ---: | ---: | ---: | --- |
| Sol 5.6 | 200 | 42.0% | 55.0% | 0.01094 | Endpoint improvement |
| Astra | 300 | 99.7% | 99.3% | 0.0000443 | All-round stability within 10 points |
| Luna 5.6 | 1,125 | 42.6% | 39.6% | 0.00273 | All-round stability within 10 points |

Sol's fixed exact paired test finds a 13-point improvement from round 1 to
round 5. There were 45 rollouts that changed from incorrect to correct and 19
that changed from correct to incorrect. This endpoint test does not identify
the cause of the change or prove a steady trend through every round.

Astra and Luna pass their fixed tests that all ten round-pair differences are
smaller than 10 percentage points. The simultaneous 95% upper bounds on the
five-round accuracy range are 6.70 points for Astra and 8.34 points for Luna.
Stability does not prove exact equality or high accuracy: Astra stays near 99%,
while Luna stays near 40%.

The report also provides conservative paired difference intervals. Sol's
individual 95% interval is [+1.02, +24.45] points; its seven-claim interval is
[-2.46, +27.63] points. These intervals combine separate exact bounds for gains
and losses. They are more conservative than the exact McNemar test and do not
invert that test, so the corrected interval can include zero while the corrected
test rejects no endpoint change.

Every planned rollout remains included. Luna's one missing final answer is
counted as incorrect. No terminal API errors occurred in these three complete samples.
Pending and excluded models have no raw p-value in this release. Their
correction inputs are 1. Current Bonferroni rejections are a subset of the
eventual Holm rejection set for the same fixed samples and seven candidate slots, so this
release does not add a second chance to test partial data.

| Model | R1 correct | R2 correct | R3 correct | R4 correct | R5 correct |
| --- | ---: | ---: | ---: | ---: | ---: |
| Sol, n=200 | 84 | 85 | 90 | 109 | 110 |
| Astra, n=300 | 299 | 291 | 296 | 297 | 298 |
| Luna, n=1,125 | 479 | 480 | 470 | 468 | 446 |

The plot uses pointwise 95% Wilson intervals. The primary confidence bounds
use a separate correction across the seven candidate claims.

- [Report, tests, and plots](http://bubble:8003/efficient-five-async-20260912-180236/completed-analysis-20260913-043016/)
- [Plot PNG](http://bubble:8003/efficient-five-async-20260912-180236/completed-analysis-20260913-043016/accuracy-by-round-95ci.png)
- [Plot PDF](http://bubble:8003/efficient-five-async-20260912-180236/completed-analysis-20260913-043016/accuracy-by-round-95ci.pdf)
- [Five-model progress and public transcripts](http://bubble:8003/efficient-five-async-20260912-180236/)

Reproduce the release with `scripts/analyze_color_completed_confirmation.py`.
The release directory contains its dated amendment, original plan hash, source
hashes, all tested round data, CSV tables, and PNG/PDF/SVG plots. The original
full-study watcher still waits for all five selected models and retains its
saved source and Holm procedure.
