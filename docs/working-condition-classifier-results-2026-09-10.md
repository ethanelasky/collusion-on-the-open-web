# Expanded working-condition classifier results

This report covers 135 new working-condition episodes. Kimi is pending repair validation and is excluded from this table. The 45 failed text-interface Kimi runs are retained separately and excluded here. The earlier 20 judgments remain in the original report.

Counts indicate runs with evidence labeled present, including intentions and attempts. Incomplete episodes remain separate from resolved episodes in the source-quality columns. Provider-invalid episodes are excluded from the positive counts in the table. Bypass-sharing counts are classifier output and have not received manual review. Citation repairs are recorded separately: wrong turn indices are corrected only for unique exact source matches, and unsupported quotations require a new validated judge response. Original outputs are retained.

| Model | Judged | Provider invalid | Resolved | Requests help | Shares info | Fulfills requests | Coordinates | Social expression | Uses peer info | Bypass sharing |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| gpt-5.6 | 45 | 0 | 39 | 0 | 2 | 2 | 11 | 1 | 29 | 0 |
| qwen3.8-27b | 45 | 2 | 40 | 1 | 17 | 7 | 22 | 4 | 41 | 2 |
| glm-5.3 | 45 | 2 | 23 | 15 | 29 | 13 | 36 | 17 | 43 | 4 |

The Kimi interface changed, so these 45 native-tool results should be analyzed as a distinct configuration from the original five text-tool Kimi runs. All task prompts retain the same wiki prefill.

[Docent collection](https://docent.transluce.org/dashboard/f0c4850a-a84b-41c6-a2e5-5656beac8a21). Per-model JSON/CSV summaries and evidence are stored in `judgments/working-write-on-extension-20260910/`.
