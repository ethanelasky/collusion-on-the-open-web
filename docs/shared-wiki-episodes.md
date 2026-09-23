# Shared wiki episodes on the existing runtime

The September 11 cleanup keeps the existing `episode.play`, `World` router,
environment-model simulator, preparation path, and transcript format. It does
not import the separate `sector-core` or `wiki-research` runtime.

## Small integration surface

`ai-collusion-wiki play --agents N` gives each sample N private episodes.
The existing `coord.run_episode` scheduler interleaves their original episode
loop by simulated container time. Each agent keeps its own context, task clock,
questions, score, environment-model conversation, and cache key. Only committed
wiki edits are shared. An edit is visible at or after its recorded container
time. Shell tools still use the original simulator and its explicit write
effects. No private history or future question is broadcast to peers.

The shared state is an edit log attached to the existing World wiki methods.
It retains their append-new-lines behavior; it is not a new wiki database or
replacement router. Per-agent `wiki_posts` still records only that agent's posts.
The shared log and group identity are observer metadata in saved transcripts.
Counters remain private in this integration; shared counters are a separate
experiment. No claim of shared counter communication is made.

The default is still one agent. With more agents, each gets the same selected
variant and schedule, with a distinct sampling seed. This does not introduce
a rotated workforce task or claim that the selected task requires reciprocity.
Question and prefill changes belong in later reviewed variants.

Model calls within a group are ordered, not wall-clock concurrent. `--workers`
controls concurrent independent groups. The original task clocks and simulated
tool durations remain unchanged; model response time is not charged as API
latency. `--isolated-wiki` uses separate edit logs as a control.

## Run and inspect

Use existing configured model names for the evaluated model and simulator:

```bash
uv run ai-collusion-wiki play --page DataUSAStateSequenceCollab2027 --rev 4 \
  --arms experiments/wiki-baselines.yaml --arm neutral \
  --only MODEL --env-model ENV_MODEL --agents 3 --workers 1 \
  --html --wiki-host 127.0.0.1 --wiki-port 8889 --run-id shared-wiki-check
```

The printed URL grants read access only. The HTML adapter reads the same World
wiki methods. It does not call a model, advance time, or publish edits. A private
IPv4 address, including a Tailscale address, can replace loopback. Use port 0
when multiple groups run concurrently. The server closes with its group.

Each agent produces a normal episode JSON file, with an `agent0`, `agent1`, etc.
suffix. The existing Docent command reads these files, preserves prefill labels,
and includes group metadata. Run files remain the source evidence. A partial
shared group must use a new run ID; completed group files are never overwritten.

## Condition names and preserved behavior

- `neutral`: Neutral Environment with Wiki prefill. Known sources return real values.
- `broken`: Broken Environment with Wiki prefill. Non-wiki HTTP requests fail;
  the deterministic resolver returns HTTP 503 and the simulator handles other
  failures under the same condition rules. Wiki reads and writes work.
- `empty_success`: Empty Success Environment with Wiki prefill. The existing
  source-empty condition stays unchanged, including its validation guard.
  Known source queries return HTTP 200 with `{"data":[]}`. Existing informational
  HTML fixtures still work; this is not a new rule that erases every web page.

`evil` remains a legacy-only key for reproducibility, with its old mixed-failure
semantics. It is not silently relabeled as strict `broken`. Its archived prompt
stays unchanged. New examples use the neutral names.

Swarm, self-copy, and culture variants are removed from both shipped workforce
specs. Historical runs and earlier source commits remain available. The separate
research runtime and its artifacts were not copied into main. Its local run data
and source archive remain in the original worktree, which this cleanup does not
reset or remove.

## Future changes

Add prefixes, discovery hints, wiki versions, and task settings through existing
variants, arms, and prompt assets. A change to routing, scheduling, clocks, tool
execution, or storage is an infrastructure change. Make that decision explicitly
and test it separately; do not change it while adding a content variant.
