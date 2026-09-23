# `coord` — shared multi-agent core

This is the infrastructure both experiments share, and nothing else. It is deliberately three small
pieces with one contract, so the replication track and David's covert-channel track can build on it
in parallel without touching each other.

## The contract

```
Medium.resolve(agent_id, url, at_global_s) -> MediumResult | None      # global shared state
Participant.next_due() -> float | None                                 # global seconds; None = done/blocked
Participant.take_turn(mediums, now_global_s) -> Turn
Participant.done() -> bool
run_episode(participants, mediums, max_turns=, max_global_s=) -> EpisodeResult
```

Two design commitments make this work for both tracks:

1. **Media hold global state.** One `CounterMedium` instance is shared by every participant, so a
   write by one agent is seen by the next agent that reads. This is the only thing that turns "N
   agents" into "N agents that can coordinate." (Single-agent Stage 0 keeps its own copy in `World`;
   Stage 2 will share one.)
2. **The scheduler orders turns by global time, nothing else.** Each participant says when it next
   wants to act; the earliest goes next; its writes are visible downstream. Round-robin and
   staggered-cohort relay are both just consequences of that rule, so the scheduler never grows a
   special case for either experiment.

Everything task-specific — clocks, questions, grading, secrets, prompts, whether a model is called —
lives inside a `Participant`. That is the seam along which the two tracks diverge.

## How the two tracks use it

**covert_channel (David, `experiments/covert_channel/`, built in parallel):**
`CounterMedium` (shared, or a per-agent isolated instance for the blocked-channel control) + a
`SenderParticipant` holding a uniform random secret and a `ReceiverParticipant` that reads and
guesses. Round-robin cadence. Metric = receiver accuracy vs. the 1/N baseline. See that folder's
DESIGN.md.

**replication (this repo, Stage 2, my next step — pending your OK):**
- Lift the sector61 wiki into a `WikiMedium` with the same shared-state property as `CounterMedium`
  (all cohorts read one another's posts). This is the one real refactor: today `env.World` owns the
  wiki state (`own_posts`, dump serving, `RecentChanges`); Stage 2 moves that into a shared medium
  the Worlds consult. Contained, but it touches `env.py`, so I want your confirmation first.
- Wrap a `World` as a `SectorAgentParticipant`: `next_due()` = the global time of its next intended
  action (wake from a `wait`, or when its next question arrives, via `rate` + a per-cohort offset);
  `take_turn` = one `env.step`, but wiki/counter calls resolve against the shared media.
- Run ≥2 cohorts on offset clocks (an "ahead" one that can relay R5, a "behind" one that can use it),
  or one live cohort + a `ScriptedParticipant` ahead cohort to isolate a single role.
- New metric on top of the existing `heartbeat_report`: did the behind agent's R5 improve when the
  ahead agent relayed, vs. a control where the relay medium is isolated. This is the first setup with
  a real beneficiary, so self-sacrifice finally means something.

## Open decisions for your confirmation

- **The Participant seam.** Is `next_due` / `take_turn` / `done` the right contract, or do you want
  the scheduler to own the model call (participants return an intended action, scheduler executes)?
  I chose participant-owns-its-turn so the two tracks share nothing but the protocol.
- **One counter, or per-track copies.** Right now `CounterMedium` here and `env.World._counter` are
  twins with an identical contract (a test asserts it). Converge them in Stage 2 (World delegates
  here), or keep the twin until the refactor?
- **Same-model vs cross-model, and how much protocol we pre-specify** are experiment parameters, not
  infra — they live in each track's config.
