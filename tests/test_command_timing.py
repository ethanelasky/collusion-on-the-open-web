"""Timing clarification changes model input; deltas describe existing World movement."""
from dataclasses import asdict
import re

import pytest

from ai_collusion.client import ModelConfig
from ai_collusion.env import step
from ai_collusion.episode import play
from ai_collusion.preparation import prepare_episode
from ai_collusion.wiki import reconstruct
from tests.test_sparse_costly import prepared  # noqa: F401


@pytest.mark.parametrize('call', ['shell("date -u")', 'web_fetch("https://worldtimeapi.org/api/timezone/Etc/UTC")'])
def test_explicit_start_changes_only_current_prompt_with_fixed_reply(prepared, monkeypatch, call):
    legacy, explicit = [prepared(variant='sparse_costly_v4').world for _ in range(2)]
    explicit.explicit_command_start = True
    for w in [legacy, explicit]:
        w.env_model = ModelConfig('env', 'stub', 'test')
    seen = []
    def generate(model, system, messages, **kwargs):
        seen.append((system, messages))
        return {'text': 'fixed clock output\n@@DATA_QUERIES []\n@@ELAPSED 1'}
    monkeypatch.setattr('ai_collusion.env.generate', generate)
    start = explicit.container_utc.isoformat(sep=' ')
    old, new = step(legacy, call), step(explicit, call)
    assert seen[0][0] == seen[1][0]
    assert seen[0][1][:-1] == seen[1][1][:-1]
    text = seen[1][1][-1]['content']
    assert f'container UTC {start}' in text
    assert 'WORLD STATE above is after the base charge' in text
    assert '@@ELAPSED is total command duration from command start' in text
    assert re.sub(r'COMMAND TIMING\n.*?(?=COUNTERAPI STATE)', '', text, flags=re.S) == seen[0][1][-1]['content']
    assert 'COMMAND TIMING' not in seen[0][1][-1]['content']
    for key, value in asdict(old).items():
        if key != 'env_call':
            assert asdict(new)[key] == value
    assert new.env_call.effects == old.env_call.effects
    assert explicit.task_s == legacy.task_s
    # Frozen simulator history retains the prior timing message verbatim.
    step(explicit, call)
    assert seen[2][1][:len(seen[1][1])] == seen[1][1]


def test_setting_is_validated_and_saved_without_changing_prefill(prepared):
    original = prepared(variant='sparse_costly_v4')
    w = original.world
    cut = reconstruct(w.dump, 'dse/DataUSAStateSequenceCollab2027', 4)
    cfg = w.spec.cuts[f'{cut.page_id}@{cut.seq}']['variants']['sparse_costly_v4']
    cfg['explicit_command_start'] = True
    new = prepare_episode(w.spec, w.dump, cut, 'sparse_costly_v4', 'neutral',
                          hint='search_result', wiki_write_instructions=True)
    assert new.world.explicit_command_start
    assert new.resolved_config['cut']['explicit_command_start'] is True
    assert new.context == original.context
    cfg['explicit_command_start'] = 'yes'
    with pytest.raises(ValueError, match='explicit_command_start must be a boolean'):
        prepare_episode(w.spec, w.dump, cut, 'sparse_costly_v4', 'neutral')


@pytest.mark.parametrize('case,call', [
    ('short', 'shell("date -u")'),
    ('wait', 'wait("60")'),
    ('invalid', 'no tool'),
    ('cancel', 'shell("date -u")'),
    ('env_error', 'shell("date -u")'),
])
def test_episode_deltas_include_early_returns_and_errors(prepared, monkeypatch, case, call):
    p = prepared(variant='sparse_costly_v4')
    w = p.world
    w.explicit_command_start = True
    w.env_model = ModelConfig('env', 'stub', 'test')
    if case == 'cancel':
        w.task_s = w.next_round().asked - 1
    def generate(*args, **kwargs):
        if case == 'env_error':
            raise RuntimeError('offline provider failure')
        if case == 'cancel':
            raise AssertionError('cancelled before simulation')
        return {'text': 'fixed\n@@DATA_QUERIES []\n@@ELAPSED 1'}
    monkeypatch.setattr('ai_collusion.env.generate', generate)
    before_task, before_utc = w.task_s, w.container_utc
    result = play(model=ModelConfig('agent', 'stub', 'test', stub_text=call),
                  world=w, context=p.context, prepared=p, temperature=None, seed=0, max_turns=1)
    turn = result['episode']['turns'][0]
    elapsed = turn['elapsed']
    assert elapsed['actual_task_delta_s'] == w.task_s - before_task
    assert elapsed['actual_utc_delta_s'] == (w.container_utc - before_utc).total_seconds()
    if case == 'short':
        assert elapsed['reported_s'] == elapsed['charged_s'] == 1
        assert elapsed['actual_utc_delta_s'] > 4
    elif case == 'wait':
        assert elapsed['actual_task_delta_s'] == 39
    elif case == 'invalid':
        assert elapsed['actual_task_delta_s'] == 0
    elif case == 'cancel':
        assert turn['source'] == 'shell-cancelled'
        assert elapsed['actual_task_delta_s'] == 1
    elif case == 'env_error':
        assert result['episode']['end_reason'] == 'environment_error'
        assert turn['result'] == ''
        assert elapsed['actual_task_delta_s'] == 3
