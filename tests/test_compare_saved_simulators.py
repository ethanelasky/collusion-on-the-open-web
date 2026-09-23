"""Evidence availability follows agent decisions and subsequent tool results."""
import pytest

from scripts.compare_saved_simulators import present_by, seconds


def row_with_quotes(quotes, decisions, completions):
    return {'decision_times': decisions, 'end_times': completions,
            'judgment': {'evidence': [{'labels': ['observed'], 'quotes': quotes}]}}


@pytest.mark.parametrize('turn,decision,completion,deadline', [
    (11, '23:22:48', '23:23:04', '23:22:57'),  # G13, Q4
    (19, '23:49:09', '00:06:07', '23:49:16'),  # G40, Q5
])
@pytest.mark.parametrize('field', ['reasoning', 'text'])
def test_agent_statement_before_deadline_survives_slow_tool(turn, decision, completion, deadline, field):
    start, end = seconds(decision), seconds(completion)
    if end < start:
        end += 86400
    row = row_with_quotes([{'turn': turn, 'field': field}], {turn: start}, {turn: end})
    assert present_by(row, start - 1) == set()
    assert present_by(row, start) == {'observed'}
    assert present_by(row, seconds(deadline)) == {'observed'}


def test_mixed_evidence_requires_result_even_when_reasoning_is_available():
    row = row_with_quotes([{'turn': 1, 'field': 'reasoning'}, {'turn': 1, 'field': 'result'}],
                          {1: 100}, {1: 120})
    assert present_by(row, 110) == set()
    assert present_by(row, 120) == {'observed'}


def test_multiturn_evidence_requires_later_agent_statement():
    row = row_with_quotes([{'turn': 1, 'field': 'result'}, {'turn': 2, 'field': 'text'}],
                          {1: 100, 2: 120}, {1: 120, 2: 180})
    assert present_by(row, 119) == set()
    assert present_by(row, 120) == {'observed'}
