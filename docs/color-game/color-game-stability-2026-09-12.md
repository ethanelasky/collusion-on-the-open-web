# Color accuracy: evidence of stability across rounds

A non-significant trend does not show that accuracy stayed stable. This analysis tests whether every pair of round accuracies differs by less than a chosen margin. It uses all 1,050 pilot rollouts: seven models, three settings, and 50 rollouts per setting. Each rollout has five rounds.

**Working margin: 10 percentage points. No pilot model or setting establishes stability within that margin under this test.** The margins of 5 and 20 points are sensitivity checks. At 20 points, Gemini async passes Holm correction across all 21 settings (p = .00809). This does not establish exact equality, and the margin was not enlarged to select a significant result. The pilot analysis is retrospective.

Luna, Haiku, and GLM remain uncertain. The earlier decision to collect only Sol and DeepSeek follow-up samples targeted the largest positive endpoint changes. It did not mean that the other models had established stability. A separate fresh stability study now tests Astra and Gemini async.

## How much variation can the pilot rule out?

The range is the highest round accuracy minus the lowest round accuracy. All table values are percentage points. The upper bounds jointly cover all 21 model/setting ranges with at least 95% confidence. An observed range below 10 is insufficient; the uncertainty must also exclude a range of 10 or more.

| Model | Async observed range | Async 95% upper bound | Sync observed range | Sync 95% upper bound |
| --- | --- | --- | --- | --- |
| Astra | 6.0 | 23.3 | 10.0 | 31.6 |
| Gemini | 2.0 | 16.7 | 44.0 | 65.9 |
| Sol | 24.0 | 48.1 | 12.0 | 44.8 |
| DeepSeek | 20.0 | 50.7 | 10.0 | 38.3 |
| Luna (earlier interface) | 8.0 | 39.0 | 18.0 | 47.1 |
| Haiku | 18.0 | 45.5 | 16.0 | 41.3 |
| GLM | 14.0 | 45.7 | 14.0 | 44.3 |

For example, Gemini async varies by only 2 points in the sample, but its corrected upper bound is 16.7 points. More data can narrow that bound. Gemini sync has a 44-point observed range and a clear round-change result in the earlier analysis. Equal round 1 and round 5 values alone would not rule out a large middle-round dip.

[All 21 panels and confidence bounds, CSV](http://bubble:8003/statistical-analysis/stability/stability-summary.csv) · [All margin tests, CSV](http://bubble:8003/statistical-analysis/stability/stability-equivalence-tests.csv) · [Pair details, CSV](http://bubble:8003/statistical-analysis/stability/stability-pair-tests.csv) · [Full JSON and hashes](http://bubble:8003/statistical-analysis/stability/stability-results.json).

## Fresh study for stability

**Launched: 300 fresh async rollouts each for Astra and Gemini.** This is a separate fixed batch of 600 rollouts and 3,000 rounds. [Live progress and public transcript links](http://bubble:8003/stability-async-2x300-20260912-173506/) · [Frozen plan](http://bubble:8003/stability-async-2x300-20260912-173506/confirmation-plan.json) · [Final analysis](http://bubble:8003/stability-async-2x300-20260912-173506/analysis/).

The primary claim is that all ten pairwise differences among the five round accuracies are smaller than 10 percentage points. The two model claims use Holm correction. No pilot outcomes enter the confirmation tests. The sample size and margin stay fixed, and tests run after all jobs reach a final state. Failure of the equivalence test is inconclusive; it is not evidence of a change.

| Model | Fresh rollouts | Workers | Power lower bound under the planning scenario |
| --- | --- | --- | --- |
| Astra async | 300 | 16 | 97.43% |
| Gemini async | 300 | 8 | 99.70% |

The power calculation assumes zero true round differences. It retains the pilot distribution of the number of correct rounds in each rollout and assigns their positions uniformly. A stress scenario increases the fraction of imperfect rollouts to its one-sided 95% pilot upper confidence bound. Exact paired-test power and a union bound over ten failures give the table limits. Independent enumeration and simulations checked the calculation. These assumptions do not cover every possible unseen error pattern, model change, or provider change; they are not universal power guarantees.

The game, models, provider routes, reasoning controls, action limits, and tool behavior match the pilot. Seed 1391820968 is new. Each rollout has a fresh random namespace, agents, and counter store. Alice alone can increment counters. Both players can read them. All 300 planned rollouts per model remain in the denominator. Missing choices and absent rounds in failed jobs count as incorrect; missing completed transcripts are data-integrity errors.

The running 200-rollout Sol and DeepSeek async batches keep their original paired endpoint plan. The stability batches use separate records, public Docent collections, and analysis watchers.

## What about the other models?

More samples can also help Luna, Haiku, GLM, and the other counter settings. Their present intervals are wide. Establishing a small range at intermediate accuracy can require much larger samples than detecting a 20-point increase. The table below gives sufficient sample sizes for two selected stability claims, a 10-point margin, and a true zero-change scenario. It assumes each round pair has the stated discordance probability q, where discordance means that the same rollout changes correctness between the two rounds.

| Assumed q for each round pair | Fresh N per setting for at least 80% power | Fresh N per setting for at least 90% power |
| --- | --- | --- |
| 0.30 | 850 | 925 |
| 0.40 | 1,100 | 1,200 |

These are sufficient counts on a 25-rollout grid, not exact minimum counts. With correction for 21 claims, the corresponding 80% counts rise to 1,200 and 1,550. A wider 20-point margin would need fewer samples, but answers a different question. The full power plan covers all 21 pilot panels and shows the assumptions. No additional Luna, Haiku, or GLM batch was launched in this step.

[Power plan](http://bubble:8003/statistical-analysis/stability/power-trajectory-plan.md) · [Selected study assumptions and validation](http://bubble:8003/statistical-analysis/stability/power-trajectory-selected-design.json) · [Plans for all 21 panels](http://bubble:8003/statistical-analysis/stability/power-trajectory-all-panels.csv) · [Margin and noise sensitivity](http://bubble:8003/statistical-analysis/stability/power-trajectory-sufficient-sizes.csv).

## Method and checks

For each round pair, the independent observations are complete rollout pairs. Exact Clopper–Pearson bounds estimate the probabilities of gaining and losing correctness. At test level a, each marginal interval has confidence 1 − a. Its two tails each have error at most a/2. Subtracting those bounds gives a lower and upper bound for the accuracy difference, each with one-sided error at most a. Requiring both bounds to lie inside the margin gives a conservative two-one-sided equivalence test.

All ten round pairs must pass to establish stability. This intersection-union test uses the largest of the ten equivalence p-values. It needs no factor of ten within the model claim. Holm correction then controls error across the model/setting claims. The argument allows arbitrary dependence among rounds within a rollout. [Berger and Hsu, intersection-union tests](https://www.public.asu.edu/~rlberge1/papers/statsci96.pdf).

The one-sided upper bound on total accuracy range uses the largest absolute-change bound among all ten pairs. It covers the fixed population pair that attains the true range. Bonferroni correction across 21 panels gives the simultaneous bounds in the table. This is different from interpreting a non-significant difference test as evidence of no change. [Paired binary equivalence methods](https://pubmed.ncbi.nlm.nih.gov/11414572/).

The implementation is conservative. More efficient paired equivalence methods exist, but would require their own validation. The current results match an independent calculation. Tests cover zero discordance, a dip with matching endpoints, count consistency, multiplicity, missing trajectories, and the frozen confirmation plan.

Reproduce the pilot audit from the repository root with the statistical Python environment:

```bash
reports/color-game/.plot-venv/bin/python scripts/analyze_color_stability.py --results reports/color-game/six-models-3x50-20260912-074117/statistical-analysis/statistical-results.json --out reports/color-game/six-models-3x50-20260912-074117/statistical-analysis/stability
reports/color-game/.plot-venv/bin/python -m pytest tests/test_color_stability.py tests/test_color_stability_confirmation.py tests/test_color_confirmation.py -q
```

The fresh stability watcher uses scripts/analyze_color_stability_confirmation.py. It runs a saved source copy, checks the frozen plan hash on each pass, and writes results only after all planned jobs are terminal. These analysis scripts make no model calls.
