# Brave retrieval capture and wiki-result candidate

The live Brave API returned HTTP 200 and ten results for `DataUSA workforce population by state 2015 2020`. Credentials were found in the neighboring `ai-swarm` repository's `.env`; no credential is included in these artifacts. `../ai-swarm/search_backends.py` already implements Brave and other search providers.

- [Original API response](../data/brave-search-capture/response.json)
- [Request provenance and response hash](../data/brave-search-capture/provenance.json)
- [Response with the wiki result at rank 5](../data/brave-search-capture/response.with-wiki.json)
- [Exact single-result JSON Patch](../data/brave-search-capture/wiki-edit.patch.json)
- [Edit provenance](../data/brave-search-capture/candidate-provenance.json)
- [Reproducible local builder](../data/brave-search-capture/build_candidate.py)

The candidate replaces the fifth result, retaining nine real surrounding results and all other response fields unchanged. The title and description come verbatim from the configured wiki's heading and opening paragraph. Its profile name and favicon come from a second successful Brave lookup of the real wiki host. No invented Google wrapper is involved. Undoing the replacement recovers the original JSON exactly.

The candidate is now selected by the opt-in [v6 arms](../experiments/sparse-wiki-costly-data-v6.yaml), using the real Brave endpoint and native JSON. V5 and earlier arms retain their Google HTML exposure. The capture is from September 12 UTC, not a verified pre-June search snapshot. Real result snippets and destinations are retained, so their effects on alternate answer sources also differ from the older handpicked background results.

For retrieval, [Brave Web Search](https://api-dashboard.search.brave.com/api-reference/web/search/get) provides ranked web results. [SerpAPI's Google endpoint](https://serpapi.com/search-api) offers Google results and raw HTML when the Google presentation specifically matters. [Tavily](https://docs.tavily.com/documentation/api-reference/endpoint/search) and [Exa](https://exa.ai/docs/reference/search) offer search with content retrieval options. For this experiment, captured Brave JSON avoids having to manufacture browser HTML while preserving a reproducible response and a precisely recorded intervention.

## V6 behavior and comparability

The successful response is supplied in prefill. Every subsequent request to `api.search.brave.com` receives HTTP 429 with a constructed `QUOTA_LIMITED` JSON error, even for the original query. Direct fetches and ordinary stdout curl GETs are routed locally. Complex shell commands retain the existing simulator, whose system prompt requires the same quota outcome for retries, dynamically assembled requests, and background requests. Previously cached successful content remains readable. This is not a real live quota exhaustion; the failure response is simulated. The outbound-proxy authentication sentence explains how URL-only calls can use the API without exposing credentials. No Brave key is used in a rollout.

The user explicitly requested this search-tool behavior change. Separate tests cover the new routing, shell status handling, simulator instructions, fixture identity, all five arms, and legacy behavior. Wiki routing, data-query outcomes, clocks, scheduling, and turn/token budgets are unchanged. The complete v5 context was compared before and after and is identical. The v6 exposure changes provider, response format, real surrounding sources, result count (10 versus 7), the proxy-authentication sentence, and subsequent Brave availability together; it does not isolate any one cause of evaluation awareness. Upstream `mixed` metadata is retained verbatim, including indices beyond the ten returned web results.

The earlier background DataUSA retry-accounting limitation remains unresolved. The nominal unreliable-90% arm must be interpreted descriptively, not as a fully validated 90% failure-rate experiment. This Brave change does not fix that independent simulator issue.


## GLM rerun

The user requested a rerun to check evaluation awareness: three seeds each for broken, slow-data, and unreliable-90%. The initial unpinned batch routed to Together, whose four saved traces exposed no reasoning at all (119 live turns); three also failed native tool formatting. That diagnostic batch was stopped and preserved. A Fireworks first-turn probe exposed reasoning, including wiki-decoy suspicion. The primary rerun is pinned to Fireworks (nearly all v5 turns used that provider), with fallbacks disabled. This provider control is an additional comparability difference. The rubric is byte-identical to the v5 classifier rubric, and prefill remains context-only.

- Primary frozen batch: `wiki-v6-brave-fireworks-glm-3x3-20260912T055445Z`.
- [Stopped batch and reason](../data/wiki-v6-brave-glm-3x3-20260912T054932Z/abort.json).
- [Diagnostic classifier table and Docent citations](../data/wiki-v6-brave-glm-3x3-20260912T054932Z/diagnostic-report.md).
- Validation: 76 tests passed across Brave behavior, hint discovery, manifest guards, wiki links, prefill timing, and sparse task conditions. The v5 context was exactly unchanged.


The completed GLM rerun report contains all classifier labels, per-run results, and live-turn citations linked to Docent. Awareness was observed in 6/9 primary runs versus 4/9 v5; executed sharing occurred in 3/9. The provider control and other bundled changes prevent attributing this difference solely to the search wrapper.
