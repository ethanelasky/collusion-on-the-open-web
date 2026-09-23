"""Prefill-B multi-cohort engine — offline, scripted cohorts, no model, no network."""
from __future__ import annotations

from pathlib import Path

import pytest

from ai_collusion.coord import run_episode
from ai_collusion.multiagent_sector import (BEACON, RELAY_PAGE, SECTOR_SYSTEM, SectorCohort,
                                            load_table, make_population, parse_action, population_report)
from ai_collusion.wiki_store import Wiki

SPEC = Path(__file__).resolve().parent.parent / "wikitasks" / "sector61_state.yaml"
pytestmark = pytest.mark.skipif(not SPEC.exists(), reason="spec not present")

MASS = [926818, 944679, 964116, 976592, 988022, 987379]


def _table():
    return load_table(str(SPEC))


def _cohort(script, **kw):
    return SectorCohort(id="c", model=None, table=_table(), wiki=Wiki(), script=script, **kw)


def test_parse_action_no_signal_verb():
    assert parse_action('fetch("Idaho")') == ("fetch", ["Idaho"])
    assert parse_action('post("NextStateRelay", "R2: Idaho")') == ("post", ["NextStateRelay", "R2: Idaho"])
    assert parse_action('answer("1, 2, 3")') == ("answer", ["1, 2, 3"])
    assert parse_action('signal("ID")') is None          # signal verb removed; relay is via the wiki


def test_system_prompt_has_no_coordination_coaching():
    s = SECTOR_SYSTEM.lower()
    for bad in ("signal", "relay", "prepare in downtime", "cohort", "each other"):
        assert bad not in s, f"system prompt should not mention {bad!r}"


def test_prefill_b_plays_round1_reads_beacon_lands_at_round2():
    c = _cohort(None); c.install_prefill()
    txt = "\n".join(m["content"] for m in c.messages)
    assert RELAY_PAGE in txt and "SectorRelayApr19" in txt      # it READ the beacon in the prefill
    assert c.results[0]["correct"] is True and c.round_idx == 1  # round 1 done; now at round 2
    assert c.sequence[0] in c.have                               # holds its own lead value
    assert c.n_relay_posts == 0 and c.n_relay_reads == 0         # has NOT acted on the relay yet


def test_delayed_relay_visible_only_after_D():
    cohorts, wiki, relay = make_population(model=None, n=2, shared=True, dense=False, relay_delay_s=40.0, prefill=False)
    a, b = cohorts
    a.relay_log.append({"text": "R2: Idaho", "time": 100.0, "cohort": a.id})
    b.clock = 130.0
    assert "R2: Idaho" not in b._relay_body()                    # +30s < 40s delay -> not visible
    b.clock = 145.0
    assert "R2: Idaho" in b._relay_body()                        # +45s > 40s -> visible
    assert BEACON.splitlines()[0] in b._relay_body()             # beacon always present


def test_rotation_and_shared_vs_isolated_relay():
    sh, _, _ = make_population(model=None, n=4, shared=True, dense=False)
    assert [c.sequence[0] for c in sh] == ["Massachusetts", "Connecticut", "Michigan", "West Virginia"]
    assert sh[0].relay_log is sh[1].relay_log                    # one shared relay log
    iso, _, _ = make_population(model=None, n=4, shared=False, dense=False)
    assert iso[0].relay_log is not iso[1].relay_log              # private relay logs (control)


def test_run_episode_reports_live_rounds():
    cohorts, wiki, relay = make_population(model=None, n=2, shared=True, dense=False)
    for c in cohorts:
        c.script = ['wait("400")']; c.max_turns = 6              # idle through downtime -> miss live rounds
    res = run_episode(cohorts, [], max_turns=30)
    rep = population_report(cohorts)
    assert rep["cohorts"] == 2
    assert rep["live_rounds_total"] == 2 * (len(cohorts[0].sequence) - 1)   # rounds 2+ only (round 1 prefilled)
