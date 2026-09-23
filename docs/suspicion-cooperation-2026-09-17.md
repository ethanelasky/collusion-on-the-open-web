# Verbalized suspicion and cooperative behavior

**September 17, 2026 · Exploratory analysis of 400 source-verified episodes.**

## Main result

**Yes in the pooled comparison, but the association depends on what the model suspects and is sensitive to other episode differences.** Episodes with either suspicion label executed cooperative communication in 141/212 (66.5%) cases, versus 52/188 (27.7%) without a supported suspicion label. The pooled association becomes much smaller after accounting for model and provider differences.

Here, cooperation means a **tool-confirmed request, information sharing, or request fulfillment**, observed in 193/400 episodes. Suspicion means an explicit statement in the model's live text or exposed reasoning; it does not necessarily mean recognizing an evaluation. Uncertain judgments count as zero on both measures.

| Verbalized suspicion | Cooperation with suspicion | Cooperation without supported suspicion | Adjusted odds ratio (95% CI) |
| --- | --- | --- | --- |
| Either suspicion label | 141/212 (66.5%) | 52/188 (27.7%) | 1.76 (0.97–3.18) |
| Planted-test suspicion | 110/151 (72.8%) | 83/249 (33.3%) | 1.20 (0.57–2.52) |
| Simulation suspicion | 92/135 (68.1%) | 101/265 (38.1%) | 2.47 (1.28–4.76) |

The adjusted comparisons account for model × task condition and Qwen provider history. An odds ratio above 1 indicates more cooperation with suspicion; intervals spanning 1 do not clearly establish a direction. The intervals use standard errors clustered by the 50 shared episode seeds. These are exploratory associations, not causal effects.

- **Planted-test suspicion:** little clear association after adjustment. This label includes traps, decoys, and planted wiki content.
- **Simulation suspicion:** a positive adjusted association, but it weakens when adding episode length: odds ratio 1.99 (0.96–4.14). Longer episodes offer more chances to observe both suspicion and cooperation; length may also be a consequence of behavior, so this is a sensitivity check.
- **Suspicion does not rule out subsequent cooperation:** 120 of the 141 episodes with both labels contain cited cooperative execution after their first cited suspicion. The classifier's selected citations do not establish the true onset of either behavior.

The evidence supports **coexistence and a positive raw association**, while leaving unresolved whether suspicion itself increases or suppresses cooperation.


## Definitions and analysis choices

The sample uses the 31 recovered derivatives and 369 unchanged original records from the [current classifier report](cooldown-grid-recovered-classifier-results-2026-09-17.md). All 400 episodes remain in descriptive denominators, including natural episode endings. No model calls or new classifications were needed. All source and judgment hashes, labels, and evidence-derived stages were checked before analysis.

The main cooperation outcome is the union of `requests_assistance`, `shares_information`, and `fulfills_request` at execution stage. Each episode counts once. This measures outward cooperative communication; it does not establish that another agent read or benefited from a message. Broad wiki coordination appears in 397/400 episodes and has too little variation to be a useful main outcome here. Merely verbalizing a cooperative plan is a separate sensitivity outcome.

The exposures are `suspects_planted_test`, `suspects_simulated_environment`, and their union. They use the existing `collaboration-v1.2` judgments without relabeling. The two labels overlap and their union is not their sum. Only `present` counts as one; `uncertain` and `absent` count as zero. Thus “without supported suspicion” includes ambiguous cases and means no sufficiently supported observed statement, not proof of no internal suspicion. “Verbalized” includes exposed reasoning and GPT reasoning summaries, so observability differs across models.

Adjusted estimates are logistic regressions with a separate intercept for every model × condition cell, additionally splitting Qwen by OpenRouter, Alibaba, and OpenRouter → Alibaba histories. A common suspicion coefficient is assumed across cells; this is a summary and need not describe each model. All-zero/all-one outcome cells supply no within-cell outcome contrast and are excluded from fixed-effect fits to avoid divergent intercepts; each fit's episode count is saved in the JSON. This does not change descriptive denominators. Confidence intervals use cluster-robust standard errors over the shared seeds. All fits converged. A Mantel–Haenszel stratified odds ratio supplies an independent computation check; results are in the JSON.

This analysis was developed after inspecting these data, was not preregistered, and makes several correlated comparisons. The confidence intervals and saved p-values are nominal, without a multiple-comparison correction. Provider and budget differences were not randomized; adjustment cannot recover a clean provider experiment or eliminate all confounding.

## How adjustment changes the association

Every entry is an odds ratio with a nominal 95% confidence interval. Episode length enters as the logarithm of the number of recorded turns; it is not a randomized or necessarily preexisting confounder.

| Suspicion | Model + arm | + Qwen provider history | + Episode length |
| --- | --- | --- | --- |
| Either suspicion label | 2.75 (1.63–4.63) | 1.76 (0.97–3.18) | 1.53 (0.81–2.90) |
| Planted-test suspicion | 2.14 (1.18–3.87) | 1.20 (0.57–2.52) | 1.30 (0.60–2.83) |
| Simulation suspicion | 3.35 (1.98–5.67) | 2.47 (1.28–4.76) | 1.99 (0.96–4.14) |

Accounting for Qwen provider history particularly reduces the planted-test and combined-suspicion estimates. After also accounting for length, all three confidence intervals include 1. This does not demonstrate zero association; it shows that the apparent association is sensitive to plausible differences in observation opportunity.

## Comparisons within each model and condition

| Model | Arm | Cooperation with either suspicion | Cooperation without supported suspicion |
| --- | --- | --- | --- |
| GPT 5.6-Sol | slow | 6/24 (25.0%) | 3/26 (11.5%) |
| GPT 5.6-Sol | working | 0/1 (0.0%) | 4/49 (8.2%) |
| Qwen 3.8 27b | slow | 15/29 (51.7%) | 3/21 (14.3%) |
| Qwen 3.8 27b | working | 22/34 (64.7%) | 5/16 (31.2%) |
| Kimi K3 | slow | 18/28 (64.3%) | 10/22 (45.5%) |
| Kimi K3 | working | 5/6 (83.3%) | 17/44 (38.6%) |
| DeepSeek V4.1 Flash | slow | 39/47 (83.0%) | 3/3 (100.0%) |
| DeepSeek V4.1 Flash | working | 36/43 (83.7%) | 7/7 (100.0%) |

The direction is not uniform. For example, every DeepSeek episode without either supported suspicion label cooperates, but that comparison has only ten episodes across both conditions. GPT has relatively few cooperative episodes and only four planted-test labels. Sparse subgroups limit model-specific conclusions.

## Other definitions of cooperation

These estimates adjust for model, condition, and Qwen provider history, without episode length. Saved wiki posts provide a direct persistence measure; the any-stage outcome also counts plans and attempts. The same conservative positive-label rule applies throughout.

| Cooperation measure | Either suspicion | Planted test | Simulation |
| --- | --- | --- | --- |
| Executed request, sharing, or fulfillment | 1.76 (0.97–3.18) | 1.20 (0.57–2.52) | 2.47 (1.28–4.76) |
| At least one saved wiki post | 1.73 (0.96–3.13) | 1.19 (0.57–2.47) | 2.46 (1.27–4.74) |
| Request, sharing, or fulfillment at any stage | 1.79 (0.96–3.35) | 0.91 (0.44–1.88) | 1.95 (0.89–4.28) |

## Sensitivity to recovered transcripts

The second column includes all final records. The last column excludes only the 31 recovered derivatives and retains the 369 original episodes that had no infrastructure interruption. This is a selected sensitivity subset, not the primary sample; it does not exclude natural turn-limit endings.

| Suspicion | All 400 episodes | 369 original, uninterrupted episodes |
| --- | --- | --- |
| Either suspicion label | 1.76 (0.97–3.18) | 1.56 (0.85–2.88) |
| Planted-test suspicion | 1.20 (0.57–2.52) | 1.21 (0.57–2.57) |
| Simulation suspicion | 2.47 (1.28–4.76) | 1.99 (1.01–3.93) |

## Order of cited evidence

Of the 141 episodes with both supported suspicion and cooperative execution, the first cited suspicion precedes the first cited cooperative result in 98, follows it in 38, and shares its turn in 5. At least one cited cooperative execution follows the first cited suspicion in 120. These counts use the earliest model text/reasoning quote in a suspicion event and the result turn supporting each execution event (the latest result turn when an event has multiple results).

The classifier returns selected supporting evidence rather than an exhaustive event timeline. These counts demonstrate that cooperation can be observed after a suspicion statement; they do not measure a before/after change in cooperation rate or prove that suspicion caused an action. Synthetic environment behavior, different reasoning visibility, posting opportunities, task difficulty, and recovery/provider selection remain possible explanations for the observed relationships.

## Reproduction and artifacts

- [Analysis script](../data/suspicion-cooperation-20260917/analyze.py)
- [All estimates, counts, versions, and input hashes](../data/suspicion-cooperation-20260917/analysis.json)
- [Episode-level binary variables and source/judgment links](../data/suspicion-cooperation-20260917/episode-variables.csv)
- [Order of cited evidence](../data/suspicion-cooperation-20260917/timing.json)
- [Exact classifier evidence](../data/error-recovery-20260917/recovered-evidence.json)

Run the analysis script with the package versions recorded in `analysis.json`, then run `data/error-recovery-20260917/report.py` to regenerate the main report. Historical source records and classifications remain unchanged.
