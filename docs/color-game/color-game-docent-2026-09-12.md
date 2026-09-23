# Public color-game transcripts — September 12, 2026

Each collection has public read access. Both the collection and a sample
transcript were checked without authentication. Collections receive saved
rollouts as they finish; the links below are stable.

| Model | Planned rollouts | Public Docent collection |
|---|---:|---|
| Luna 5.6 (earlier batch) | 150 | [Open transcripts](https://docent.transluce.org/dashboard/b9d4965b-df50-402e-a87a-95fbfcd6e3d9) |
| GLM 5.3 | 150 | [Open transcripts](https://docent.transluce.org/dashboard/1bd46cbe-c607-4afe-a19f-1133c9890dd3) |
| DeepSeek V4 Flash | 150 | [Open transcripts](https://docent.transluce.org/dashboard/cdaa2462-b9a5-4944-a889-80959eba83b0) |
| Gemini 3.8 Flash | 150 | [Open transcripts](https://docent.transluce.org/dashboard/e691a001-cb2c-4068-9c5d-9a71f0486832) |
| Haiku 4.5 | 150 | [Open transcripts](https://docent.transluce.org/dashboard/e0670e5d-c09f-461b-befc-47333e00beb0) |
| Astra | 150 | [Open transcripts](https://docent.transluce.org/dashboard/b7c72380-f2c4-4a7a-8948-72d0606ae478) |
| Sol 5.6 | 150 | [Open transcripts](https://docent.transluce.org/dashboard/377654a3-78a9-4330-8b84-90f634c39cb3) |

Each model has 50 rollouts in each of three settings: guessing only, sequential
counter, and simultaneous counter. Each rollout has five rounds, eight possible
colors, and up to eight actions per player per round. The simultaneous setting
has a shared 180-second limit per round, including final choices.

Each Docent run contains separate Alice and Bob transcripts. Returned reasoning,
actions, counter results, errors, model settings, and source metadata are retained.
Round outcomes are research metadata; the players did not receive score feedback.
Late replies are marked as audit data and do not affect scores.

The Luna batch used the earlier action interface. The six-model comparison uses
the separate GET, choose, and wait tool schemas. Use the saved prompt and adapter
versions when comparing results. Superseded GLM and DeepSeek attempts are retained
locally and excluded from these comparison collections.

The source campaigns are `luna-3x50-20260912-053510` and
`six-models-3x50-20260912-074117` under `reports/color-game/`. The comparison's
`docent-sync.json` stores upload receipts and public-access checks. See
[the export instructions](../color-game.md) to resume publication.
