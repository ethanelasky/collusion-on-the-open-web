# Classifier behavior figure

Frozen September 17 table: seven labels, four models, two arms, 50 episodes per cell. Counts are checked against all 400 selected episodes when the local recovered manifest is available. All values are supported positives; uncertain judgments remain in the denominator.

PDF and SVG are vector exports; PNG is 300 dpi. The canvas is 6.75 × 3.2 inches. Use `figure.tex` for a full-width paper figure. `data.csv` preserves the exact counts.

From the repository root:

```sh
uv run --no-project --with matplotlib==3.11.2 python scripts/plot_classifier_behaviors.py
```
