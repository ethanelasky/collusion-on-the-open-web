# Classifier results for social sharing

Use the square lead image first and the full breakdown second. Both give the task context and define the measurement on the image. The original paper figures remain unchanged.

- [Lead image: help-seeking (1600 × 1600)](help-seeking-social.png) · [PDF](help-seeking-social.pdf) · [SVG](help-seeking-social.svg)
- [All seven categories (1600 × 2000)](behavior-breakdown-social.png) · [PDF](behavior-breakdown-social.pdf) · [SVG](behavior-breakdown-social.svg)
- [Short post draft](post-draft.txt) · [Full caption and limitations](caption.txt) · [Unchanged counts](data.csv) · [Build verification](build.json)

Reproduce from the repository root:

```sh
uv run --no-project --with matplotlib==3.11.2 python scripts/plot_classifier_behaviors_social.py
```

The builder checks all 56 counts against the final 400-episode manifest and verifies that every text element fits within its canvas. Values use the existing all-stage classifier table, not the separate execution-only table.
