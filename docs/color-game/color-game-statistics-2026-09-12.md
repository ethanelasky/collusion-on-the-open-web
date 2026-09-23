# Color game: accuracy by round, confidence intervals, and test plan

Analysis date: 12 September 2026. **1,050 completed rollouts; 5,250 rounds; seven models; three settings.** Each model and setting has 50 rollouts with five rounds. Each round has eight possible colors. Random guessing gives 12.5% expected accuracy.

**No linear trend passes correction across all 21 model and setting tests.** Sol async has a positive round 1 to round 5 change under the endpoint test family, but this result does not pass the stricter correction across all 63 tests. Gemini sync has a clear change across rounds, mainly a drop after round 1. These are exploratory findings because the accuracy plots were inspected before this analysis.

| Setting | Round accuracies, R1 to R5 | Main result |
| --- | --- | --- |
| Sol async | 40%, 46%, 50%, 50%, 64% | R5 − R1: +24 percentage points; paired 95% CI +1.3 to +42.3; endpoint adjusted p = .038. Linear trend adjusted p = .057. |
| Gemini sync | 94%, 54%, 50%, 56%, 76% | Any-round change adjusted p = .000100. The strongest pair is R1 versus R3: −44 percentage points. |
| DeepSeek async | 30%, 48%, 34%, 38%, 50% | R5 − R1: +20 percentage points; paired 95% CI −7.1 to +43.8; endpoint adjusted p = .786. |

Adjusted p-values above use Holm correction within each family of 21 tests. With one correction across all 63 tests, Sol async has endpoint p = .114 and Gemini sync has any-round p = .000300. The Sol result is a candidate for a fresh test, not firm evidence of improvement. None of these tests alone identifies learning as the cause of a change.

## Accuracy and uncertainty

![Accuracy by round for seven models, with 95% confidence intervals](http://bubble:8003/statistical-analysis/accuracy-by-round-95ci.png)

Each point uses 50 independent rollouts. The bars are pointwise 95% Wilson intervals. They do not give 95% joint coverage for all 105 points. The CSV also contains exact intervals with Bonferroni correction for joint coverage across all 105 points. Use paired tests to assess round changes; overlap between two pointwise bars is not a test. [NIST interval methods](https://www.itl.nist.gov/div898/handbook/prc/section2/prc241.htm).

![Change in accuracy from round 1 to round 5, with paired 95% intervals](http://bubble:8003/statistical-analysis/round5-minus-round1-95ci.png)

The change bars are conservative 95% paired intervals. They preserve the pairing of each rollout. They remain nonzero in width when no rollout changes from correct to wrong or from wrong to correct. These bars are pointwise. Family-corrected endpoint intervals are also in the test CSV. The interval construction is conservative and is not an inversion of the exact McNemar test, so its threshold need not match that test.

## Full test table

All changes and interval limits are in percentage points. “Trend p” tests the mean linear slope through all five rounds. “R5 − R1 p” tests the paired endpoint change. “Any-round p” tests whether at least one of the ten round pairs differs. All three columns use correction within their separate families of 21 tests; the CSV also includes the combined 63-test correction. A p-value above .05 does not establish no change.

| Model | Setting | R1 | R5 | Change | Paired 95% CI | Trend p | R5 − R1 p | Any-round p |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Astra | Guessing | 12% | 6% | -6.0 | [-25.2, +14.3] | 1.000 | 1.000 | 1.000 |
| Astra | Async counter | 100% | 100% | +0.0 | [-8.4, +8.4] | 1.000 | 1.000 | 1.000 |
| Astra | Sync counter | 98% | 94% | -4.0 | [-18.2, +11.1] | 1.000 | 1.000 | 1.000 |
| Gemini 3.8 Flash | Guessing | 10% | 10% | +0.0 | [-20.8, +20.8] | 1.000 | 1.000 | 1.000 |
| Gemini 3.8 Flash | Async counter | 100% | 100% | +0.0 | [-8.4, +8.4] | 1.000 | 1.000 | 1.000 |
| Gemini 3.8 Flash | Sync counter | 94% | 76% | -18.0 | [-37.6, +4.9] | 1.000 | 0.449 | 0.0001 |
| Sol 5.6 | Guessing | 8% | 14% | +6.0 | [-15.9, +26.8] | 1.000 | 1.000 | 1.000 |
| Sol 5.6 | Async counter | 40% | 64% | +24.0 | [+1.3, +42.3] | 0.057 | 0.038 | 0.366 |
| Sol 5.6 | Sync counter | 30% | 40% | +10.0 | [-15.7, +34.1] | 1.000 | 1.000 | 1.000 |
| Luna 5.6 (earlier) | Guessing | 14% | 10% | -4.0 | [-24.4, +17.1] | 1.000 | 1.000 | 1.000 |
| Luna 5.6 (earlier) | Async counter | 42% | 36% | -6.0 | [-26.8, +15.9] | 1.000 | 1.000 | 1.000 |
| Luna 5.6 (earlier) | Sync counter | 42% | 28% | -14.0 | [-38.5, +12.8] | 1.000 | 1.000 | 1.000 |
| DeepSeek V4 Flash | Guessing | 12% | 14% | +2.0 | [-19.7, +23.4] | 1.000 | 1.000 | 1.000 |
| DeepSeek V4 Flash | Async counter | 30% | 50% | +20.0 | [-7.1, +43.8] | 1.000 | 0.786 | 1.000 |
| DeepSeek V4 Flash | Sync counter | 18% | 28% | +10.0 | [-13.3, +31.6] | 1.000 | 1.000 | 1.000 |
| Haiku 4.5 | Guessing | 10% | 18% | +8.0 | [-14.7, +29.2] | 1.000 | 1.000 | 1.000 |
| Haiku 4.5 | Async counter | 26% | 26% | +0.0 | [-25.7, +25.7] | 1.000 | 1.000 | 1.000 |
| Haiku 4.5 | Sync counter | 36% | 26% | -10.0 | [-32.9, +14.6] | 0.909 | 1.000 | 1.000 |
| GLM 5.3 | Guessing | 16% | 6% | -10.0 | [-28.3, +10.2] | 1.000 | 1.000 | 1.000 |
| GLM 5.3 | Async counter | 46% | 36% | -10.0 | [-35.1, +16.7] | 1.000 | 1.000 | 1.000 |
| GLM 5.3 | Sync counter | 14% | 14% | +0.0 | [-20.8, +20.8] | 1.000 | 1.000 | 1.000 |

## Repeated round methods

The independent sampling unit is a rollout. The five rounds within a rollout can depend on the same model history, target sequence, and counter store. The analysis keeps all five observations together. It does not treat 250 round outcomes as 250 independent samples. Five aggregate time points are not enough for a useful ARIMA analysis. These results use repeated-measures contrasts instead.

For the linear trend, each rollout contributes the slope b = [−2Y1 − Y2 + Y4 + 2Y5]/10, where Yr is 1 for a correct match and 0 otherwise. A two-sided t test uses the 50 slopes, with 49 degrees of freedom. The slope interval and test are approximate; the saved results flag sparse nonzero slopes near the accuracy ceiling. This was selected as the main trend measure for this retrospective analysis, not registered before data collection.

For R5 minus R1, gains are rollouts that change from wrong to correct; losses change from correct to wrong. The exact two-sided McNemar test uses a binomial test on gains among gains plus losses. Sol async has 13 gains and one loss, giving raw p = .001831. [Exact McNemar definition](https://www.statsmodels.org/stable/generated/statsmodels.stats.contingency_tables.mcnemar.html).

The paired change interval subtracts exact binomial bounds for the gain and loss probabilities. Two 97.5% intervals have at least 95% joint coverage by Bonferroni. Subtracting the upper loss bound from the lower gain bound, and the lower loss bound from the upper gain bound, gives the reported conservative interval. Family intervals use the same construction with a further factor of 21. [Exact binomial intervals](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats._result_classes.BinomTestResult.proportion_ci.html).

For any-round change, calculate all ten exact paired round tests and set the global p-value to min(1, 10 × the smallest pair p-value). Then correct across models and settings. This permits unequal correlations among round outcomes. Ordinary Cochran Q and within-rollout round shuffling need extra assumptions that persistent game state may violate. [Cochran Q under unequal correlation](https://www.tandfonline.com/doi/abs/10.1080/01621459.1973.10481461).

Holm correction controls the family-wise probability of at least one false rejection without requiring independence between tests. Three separate 21-test families answer three different questions. The 63-test correction covers all three questions together. The files also contain Holm correction for all 210 individual round-pair tests. Since the data were already inspected, these corrections do not convert the results into a pre-planned confirmation study. [Holm implementation](https://www.statsmodels.org/stable/generated/statsmodels.stats.multitest.multipletests.html).

## Failures, data checks, and limits

All 50 rollouts remain in each primary denominator. A missing final answer counts as wrong. An API error that is recovered does not by itself make a correct final answer wrong. Choices were checked against saved scores, round indices, rollout indices, campaign settings, and campaign totals. The data export includes source identifiers and file hashes. Late responses after the shared deadline do not change the recorded final choices.

Two diagnostic analyses keep only rollouts with all five rounds marked API-valid, or only rollouts with both final answers in all five rounds. These exclusions can bias the sample; they do not replace the main results. Sol async is unchanged. Gemini sync retains its clear round change: adjusted p = .000100 with 49 API-valid rollouts and .000200 with 48 rollouts that have both final answers throughout.

Luna used an earlier action format and is labelled separately in the plots. Model labels refer to the saved API routes. The tests measure changes within each model and setting. They do not test differences between models or prove that a change was caused by learning. Target color, persistent counter contents, previous actions, and submission failures can also affect round accuracy. Astra and Gemini async both start at 100% in this pilot, leaving little observed room for improvement.

An independent implementation checked 420 values across all 21 settings; the largest absolute difference was 1.2 × 10⁻¹⁴. The inference and power test suites passed 61 tests. Exact endpoint power was checked against direct enumeration and three simulations of 200,000 experiments each. The files include the validation results.

## How many fresh rollouts?

Use a fixed sample size selected before a fresh confirmation run. **Do not continue adding batches until p falls below .05.** The inspected 50-rollout pilot should remain separate from the new test sample. No new model rollouts were launched for this analysis.

The planning assumption is a useful R5 minus R1 change of **10 percentage points**. This is an assumption for review, not a user-selected threshold. Power depends on q, the chance that a rollout changes correctness between R1 and R5. Pilot estimates are .28 for Sol async and .40 for DeepSeek async, with substantial uncertainty. The table below uses q = .60 as a sensitivity case. It is not a universal upper bound; q can be as large as 1.

| Fresh design | Rollouts per selected setting | Total new rollouts | Power for a 10-point change, q = .60 |
| --- | --- | --- | --- |
| Sol async and DeepSeek async; two tests | 600 | 1,200 | 81.1% per test |
| Sol async and DeepSeek async; two tests | 800 | 1,600 | 91.6% per test |
| All seven models and all three settings; 21 tests | 917 | 19,257 | At least 80% per test under this common assumption |

The two-setting plans use alpha = .025 per test. The 21-setting plan uses alpha = .05/21. These are conservative plans for a final Holm analysis. Power is the probability of detecting a specified true change in one test; it is not the probability that the effect is real, and not the probability that all settings reach significance. The all-setting count shows the cost of broad testing. It is not a recommendation for models that are already near 100%.

| Useful endpoint change | Two selected tests: fresh N per setting for 80% / 90% power | 21 tests: fresh N per setting for 80% / 90% power |
| --- | --- | --- |
| 5 percentage points | 2,317 / 3,012 | 3,646 / 4,510 |
| 10 percentage points | 586 / 758 | 917 / 1,132 |
| 20 percentage points | 148 / 190 | 229 / 281 |

These counts use exact prospective power for the paired endpoint test. If D is the number of changed outcomes, D follows Binomial(N, q). Conditional on D, the gains follow Binomial(D, (q + delta)/(2q)). The calculation sums the exact test rejection probability over D. It uses pilot data only to estimate noise, not to set the target effect to the observed change. Numerical tail omission is less than 2 × 10⁻¹².

To support “no useful change,” first select an equivalence margin, such as ±10 percentage points, and use a paired equivalence test. An ordinary non-significant result is insufficient. The report includes separate approximate equivalence sample sizes; those need validation before a launch. Even a well-powered study can be inconclusive. [Equivalence test methods](https://pmc.ncbi.nlm.nih.gov/articles/PMC5502906/).

**Suggested next design:** confirm Sol async and DeepSeek async with 600 fresh rollouts each if the useful change is 10 percentage points and 80% power is sufficient. Use 800 each for about 90% power under q = .60. If only a larger 20-point change matters, the 80% plan needs 148 fresh rollouts per selected setting; 150 each is a lower-cost fixed design. Keep the five-round game and model routes fixed. Before launch, select the practical effect or equivalence margin, fixed N, failure rule, and test family. [Detailed sample size plan](http://bubble:8003/statistical-analysis/power-plan.md).

## Files and reproduction

[Round confidence intervals, CSV](http://bubble:8003/statistical-analysis/per-round-confidence-intervals.csv) · [All 21 test results, CSV](http://bubble:8003/statistical-analysis/round-change-tests.csv) · [All 210 paired comparisons, CSV](http://bubble:8003/statistical-analysis/all-paired-round-tests.csv) · [Full data, CSV](http://bubble:8003/statistical-analysis/rollout-round-data.csv) · [Failure sensitivity, CSV](http://bubble:8003/statistical-analysis/complete-case-sensitivity.csv).

[Full statistical output, JSON](http://bubble:8003/statistical-analysis/statistical-results.json) · [Independent check, JSON](http://bubble:8003/statistical-analysis/independent-statistical-crosscheck.json) · [Power sensitivity grid, CSV](http://bubble:8003/statistical-analysis/power-sensitivity-grid.csv) · [Power validation, JSON](http://bubble:8003/statistical-analysis/power-validation.json) · [Round plot, PDF](http://bubble:8003/statistical-analysis/accuracy-by-round-95ci.pdf) · [Change plot, PDF](http://bubble:8003/statistical-analysis/round5-minus-round1-95ci.pdf).

```bash
uv venv reports/color-game/.stats-venv
uv pip install --python reports/color-game/.stats-venv/bin/python numpy scipy statsmodels matplotlib pytest
reports/color-game/.stats-venv/bin/python scripts/analyze_color_round_statistics.py \
  --comparison reports/color-game/six-models-3x50-20260912-074117/comparison.json \
  --campaign reports/color-game/luna-3x50-20260912-053510/runs \
  --out reports/color-game/six-models-3x50-20260912-074117/statistical-analysis
reports/color-game/.stats-venv/bin/python scripts/plot_color_round_statistics.py \
  --results reports/color-game/six-models-3x50-20260912-074117/statistical-analysis/statistical-results.json \
  --out reports/color-game/six-models-3x50-20260912-074117/statistical-analysis
reports/color-game/.stats-venv/bin/python scripts/plan_color_round_power.py --results reports/color-game/six-models-3x50-20260912-074117/statistical-analysis/statistical-results.json --out reports/color-game/six-models-3x50-20260912-074117/statistical-analysis --sample-n 150 200 600 800
reports/color-game/.stats-venv/bin/python -m pytest tests/test_color_round_statistics.py tests/test_color_round_power.py -q
```

Run these commands from the repository root with the saved campaign files available. These analysis scripts make no model API calls. Raw campaign files and generated report assets are local report artifacts; they are not committed to Git. The repository contains the analysis code, tests, and this report text. The supplied script hashes and package versions record the calculation used here.
