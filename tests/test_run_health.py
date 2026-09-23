from ai_collusion.judge import source_facts
from ai_collusion.run_health import health


def test_embedded_provider_error_cannot_enter_valid_denominator():
    record = {"error": None, "episode": {"end_reason": "no_call", "turns": []},
              "response": {"finish_reason": "error", "raw": {"choices": [{"error": {"code": 502}}]}}}
    facts = source_facts(record)
    assert facts["source_transport_status"] == "invalid"
    assert facts["provider_response_error"] and facts["halt_reason"] == "provider error"


def test_interface_claims_are_observations_not_provider_errors():
    record = {"error": None, "episode": {"end_reason": "no_call", "turns": [
        {"call": None, "response": {"text": '<|open|>tools<|sep|>', "finish_reason": "stop"}},
        {"call": None, "response": {"text": "I must not call tools.", "finish_reason": "stop"}},
    ]}}
    facts = source_facts(record)
    assert facts["interface_limited"] and not facts["source_transport_error"]
    assert facts["unparsed_native_tool_tokens"] and facts["model_reports_tools_disabled"]


def test_native_call_is_not_a_parser_failure():
    record = {"episode": {"end_reason": "all_rounds_resolved", "turns": [
        {"call": {"tool": "web_fetch"}, "response": {"text": 'web_fetch("url")', "finish_reason": "tool_calls"}}
    ]}}
    assert not health(record)["unparsed_native_tool_tokens"]


def test_batch_validator_and_judge_agree_on_provider_blocked_sources(tmp_path):
    import json
    from scripts.validate_rollouts import validate
    record = {'model': {'name': 'test'}, 'error': None, 'arm_id': 'working', 'sample_index': 0,
              'num_live_problems': 1, 'max_tokens': 4096, 'max_turns': 1,
              'episode': {'end_reason': 'max_turns', 'turns': [
                  {'response': {'text': '', 'finish_reason': 'content_filter'}}],
                  'rounds': [{'answered_at': None, 'missed': False}]}}
    (tmp_path / 'manifest.json').write_text('{}')
    path = tmp_path / 'episode.json'
    for finish, valid in [('content_filter', False), ('stop', True), ('length', False),
                          ('incomplete:content_filter', False)]:
        record['episode']['turns'][0]['response']['finish_reason'] = finish
        path.write_text(json.dumps(record))
        report = validate(tmp_path, 'test', 1, 1, max_turns=1, arm_ids=('working',))
        assert report['valid'] is valid
        assert (source_facts(record)['source_transport_status'] == 'valid') is valid


def test_successful_simulator_repair_is_not_a_failed_source():
    record = {'error': None, 'episode': {'end_reason': 'max_turns', 'turns': [
        {'env_call': {'generation_attempts': [
            {'response': {'finish_reason': 'length'}},
            {'response': {'finish_reason': 'stop'}},
        ]}}]}}
    assert source_facts(record)['source_transport_status'] == 'valid'


def test_refusal_field_is_detected_even_when_finish_reason_is_stop():
    record = {'error': None, 'response': {'finish_reason': 'stop', 'raw': {
        'choices': [{'message': {'refusal': 'Cannot comply'}}]}}}
    assert source_facts(record)['source_transport_status'] == 'invalid'
