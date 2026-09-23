"""Analysis views for the first-response search experiment; no runtime changes."""
from __future__ import annotations
import copy
import hashlib
import json


def first_response(record: dict) -> tuple[dict, str]:
    turns = record['episode']['turns']
    if turns:
        attempts = turns[0].get('generation_attempts') or []
        if attempts:
            return attempts[0]['response'], 'episode.turns[0].generation_attempts[0].response'
        return turns[0]['response'], 'episode.turns[0].response'
    attempts = (record.get('error') or {}).get('generation_attempts') or []
    if attempts:
        return attempts[0]['response'], 'error.generation_attempts[0].response'
    return {}, 'no_recorded_response'


def reasoning_view(record: dict, source_path: str, source_bytes: bytes) -> dict:
    response, location = first_response(record)
    view = copy.deepcopy(record)
    reasoning = response.get('reasoning')
    view['analysis_view'] = {
        'kind': 'first-live-reasoning-v1', 'original_source_path': source_path,
        'original_source_sha256': hashlib.sha256(source_bytes).hexdigest(),
        'response_location': location, 'original_end_reason': record['episode']['end_reason'],
        'reasoning_available': bool(reasoning and reasoning.strip()),
        'first_response_finish_reason': response.get('finish_reason'),
        'first_response_provider': (response.get('raw') or {}).get('provider'),
        'accepted_turn_response_changed': bool(record['episode']['turns']) and
            reasoning != record['episode']['turns'][0]['response'].get('reasoning'),
        'scope': 'Only first returned model reasoning, before live tool output and repair feedback.'}
    # This derivative is an analysis input, never a replacement for the raw episode.
    view['episode'] = {
        'turns': [{'turn': 1, 'response': {'text': '', 'reasoning': reasoning},
                   'result': '', 'call': None, 'source': 'analysis-only', 'notices': []}],
        'n_turns': 1, 'end_reason': 'first_response_analysis', 'rounds': [], 'wiki_posts': {}}
    view['response'] = {'text': '', 'reasoning': reasoning}
    view['messages_final'] = copy.deepcopy(view['context']['messages'])
    view['error'] = None
    view['live_start_message_index'] = len(view['context']['messages'])
    return view


def assert_reasoning_only(judgment: dict, view: dict) -> None:
    reasoning = view['episode']['turns'][0]['response'].get('reasoning') or ''
    for event in judgment['evidence']:
        assert event['stage'] == 'expression'
        for quote in event['quotes']:
            assert quote['turn'] == 1 and quote['field'] == 'reasoning'
            assert quote['quote'] in reasoning


def first_reasoning(record: dict) -> tuple[dict, str, int]:
    """First nonblank reasoning, traversing live turns and their recorded attempts."""
    for turn in record['episode']['turns']:
        responses = [(a['response'], f"episode.turns[{i}].generation_attempts[{j}].response")
                     for i,t in enumerate(record['episode']['turns']) if t is turn
                     for j,a in enumerate(turn.get('generation_attempts') or [])]
        if not responses:
            responses = [(turn['response'], f"episode.turns[{record['episode']['turns'].index(turn)}].response")]
        for response, location in responses:
            if (response.get('reasoning') or '').strip():
                return response, location, turn['turn']
    for i, attempt in enumerate((record.get('error') or {}).get('generation_attempts') or []):
        if (attempt['response'].get('reasoning') or '').strip():
            return attempt['response'], f'error.generation_attempts[{i}].response', (record['error'].get('turn') or len(record['episode']['turns'])+1)
    return {}, 'no_reasoning_recorded', 0


def first_reasoning_view(record: dict, source_path: str, source_bytes: bytes) -> dict:
    response, location, number = first_reasoning(record)
    view = reasoning_view(record, source_path, source_bytes)
    view['analysis_view'].update(kind='first-live-reasoning-v2', response_location=location,
        selected_turn=number, reasoning_available=bool(number),
        first_response_finish_reason=response.get('finish_reason'),
        first_response_provider=(response.get('raw') or {}).get('provider'),
        scope='First nonblank live reasoning, after zero or more tool-only turns; earlier results are context only.')
    prior = copy.deepcopy([t for t in record['episode']['turns'] if number and t['turn'] < number])
    for turn in prior:
        turn['response']={'text':'','reasoning':None}
    selected={'turn':number or 1,'response':{'text':'','reasoning':response.get('reasoning')},
              'result':'','call':None,'source':'analysis-only','notices':[]}
    view['episode']['turns']=prior+[selected]
    view['episode']['n_turns']=len(prior)+1
    view['response']=selected['response']
    return view
