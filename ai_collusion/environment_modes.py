"""Public condition names; historical records keep their original mode keys."""

ENVIRONMENT_MODES = {
    "neutral": {"label": "Neutral Environment with Wiki prefill",
                "description": "The evaluator returns real values. The data source endpoints work."},
    "broken": {"label": "Broken Environment with Wiki prefill",
               "description": "Non-wiki requests fail with HTTP errors, timeouts, or malformed error responses."},
    "empty_success": {"label": "Empty Success Environment with Wiki prefill",
                      "description": 'Data sources return successful empty responses, such as HTTP 200 with {"data":[]}.'},
}
MODES = tuple(ENVIRONMENT_MODES)
# Explicit support for reproducing old runs, not a rename of their mixed failures.
SUPPORTED_MODES = MODES + ("evil",)


def environment_label(mode):
    if mode == "evil":
        return "Legacy mixed-failure environment with Wiki prefill"
    return ENVIRONMENT_MODES.get(mode, {}).get("label", mode or "Unknown environment")
