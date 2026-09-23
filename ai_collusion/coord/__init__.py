"""Shared, experiment-neutral multi-agent core.

Two experiments build on this:
  - replication (ai_collusion): sector61 cohorts sharing one wiki + counter (Stage 2).
  - covert_channel (experiments/covert_channel): David's sender/receiver secret-guessing game.

Three pieces, and only these are shared:
  Medium       a stateful resource several agents touch by GET; state is global (one instance,
               shared by all participants), so a write by one agent is seen by the next reader.
  Participant  one model instance; owns its own task/clock/prompt; the scheduler only asks it when
               it next wants to act and to take a turn.
  run_episode  global-time-ordered interleaving of participants over the shared media.

The contract (these type signatures) is what both tracks agree on and develop against in parallel.
"""
from .medium import CounterMedium, Medium, MediumResult, resolve
from .participant import Participant, ScriptedParticipant, Turn
from .scheduler import EpisodeResult, run_episode

__all__ = [
    "Medium", "MediumResult", "CounterMedium", "resolve",
    "Participant", "Turn", "ScriptedParticipant",
    "run_episode", "EpisodeResult",
]
