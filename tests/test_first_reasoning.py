import copy
import json
from pathlib import Path
from experiments.first_reasoning import first_response, reasoning_view
from ai_collusion.judge import project_record
from ai_collusion.preparation import prepare_episode
from ai_collusion.wiki import load_spec, Dump, reconstruct
from ai_collusion.arms import load_arms, validate_arms


def test_first_generation_is_used_even_after_tool_repair():
    record = {'context': {'system': 'system', 'messages': [{'role': 'assistant', 'content': 'prefill intentions'}]},
              'episode': {'end_reason': 'max_turns', 'turns': [{'response': {'reasoning': 'repaired response'},
                  'generation_attempts': [{'response': {'reasoning': 'first response'}},
                                          {'response': {'reasoning': 'repaired response'}}]}]}}
    before = copy.deepcopy(record)
    view = reasoning_view(record, '/raw.json', b'raw')
    assert view['analysis_view']['accepted_turn_response_changed']
    projection = project_record(view)
    assert projection['context']['context_only']
    assert projection['turns'][0]['reasoning'] == 'first response'
    assert projection['turns'][0]['text'] == projection['turns'][0]['result'] == ''
    assert projection['turns'][0]['call'] is None
    assert record == before


def test_failed_protocol_still_preserves_initial_reasoning():
    record = {'episode': {'turns': []}, 'error': {'generation_attempts': [{'response': {'reasoning': 'original'}}]}}
    assert first_response(record)[0]['reasoning'] == 'original'
    record['error'] = {}
    assert first_response(record) == ({}, 'no_recorded_response')


def test_conditions_differ_only_in_search_exposure():
    root = Path(__file__).resolve().parents[1]
    spec = load_spec(root/'wikitasks/sector61_state_sparse.yaml')
    dump = Dump(root/'data/collusion-wiki');cut = reconstruct(dump, 'dse/DataUSAStateSequenceCollab2027', 4)
    arms = load_arms(root/'experiments/brave-google-first-turn-v1.yaml');validate_arms(arms, spec, {4: cut})
    contexts = []
    for arm in arms:
        assert arm.max_turns == 1
        p = prepare_episode(spec, dump, cut, arm.variant, arm.mode, hint=arm.hint, wiki_write_instructions=True)
        assert p.world.query_failure_probability == 0.9
        ctx = copy.deepcopy(p.context)
        assert '13 seconds' not in ctx['provenance']['search']['result_body']
        url = ctx['provenance']['search']['url']
        index = next(i for i,m in enumerate(ctx['messages']) if m['role']=='assistant' and url in m['content'])
        del ctx['messages'][index:index+2]
        ctx['provenance'].pop('search')
        contexts.append(ctx)
    assert contexts[0] == contexts[1]
