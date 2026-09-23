"""Resume must preserve input identity and never skip an incomplete transcript."""
from dataclasses import replace
import json
from pathlib import Path

import pytest

from ai_collusion.client import ModelConfig
from ai_collusion.episode import run_episodes
from ai_collusion.runner import run_contexts
from ai_collusion.run_storage import write_json
from test_episode_provenance import run_inputs


def single_turn_inputs(tmp_path):
    return dict(models=[ModelConfig('test', 'stub', 'test')],
                contexts={'base': {'system': 'original', 'messages': [{'role': 'user', 'content': 'go'}]}},
                task_id='task', reference={}, n_samples=1, out_dir=str(tmp_path / 'runs'),
                base_seed=0, temperature=None, run_id='trial', manifest_extra={'spec': 'original.yaml'})


def transcript(out):
    return next(p for p in out.glob('*.json') if p.name != 'manifest.json')


@pytest.mark.parametrize('change', ['context', 'model', 'reference', 'spec', 'seed', 'temperature'])
def test_single_turn_resume_rejects_changed_inputs_without_overwriting(tmp_path, change):
    inputs = single_turn_inputs(tmp_path)
    out = run_contexts(**inputs)
    before = {p.name: p.read_bytes() for p in out.glob('*.json')}
    if change == 'context':
        inputs['contexts']['base']['system'] = 'changed'
    elif change == 'model':
        inputs['models'] = [replace(inputs['models'][0], max_tokens=123)]
    elif change == 'reference':
        inputs['reference'] = {'answer': 'changed'}
    elif change == 'spec':
        inputs['manifest_extra']['spec'] = 'changed.yaml'
    elif change == 'seed':
        inputs['base_seed'] = 10
    else:
        inputs['temperature'] = 0.8
    with pytest.raises(ValueError, match='cannot resume'):
        run_contexts(**inputs)
    assert {p.name: p.read_bytes() for p in out.glob('*.json')} == before


def test_single_turn_resume_extends_samples_without_rewriting_existing_result(tmp_path):
    inputs = single_turn_inputs(tmp_path)
    out = run_contexts(**inputs)
    first = transcript(out)
    before = first.read_bytes()
    identity = json.loads((out / 'manifest.json').read_text())['experiment_sha256']
    run_contexts(**inputs)
    inputs['n_samples'] = 2
    run_contexts(**inputs)
    assert len(list(out.glob('*.json'))) == 3
    assert first.read_bytes() == before
    assert json.loads((out / 'manifest.json').read_text())['experiment_sha256'] == identity


@pytest.mark.parametrize('episode', [False, True])
@pytest.mark.parametrize('broken', ['{"episode":', '{}'])
def test_incomplete_transcript_is_preserved_and_retried(tmp_path, episode, broken):
    inputs = run_inputs(tmp_path) if episode else single_turn_inputs(tmp_path)
    run = run_episodes if episode else run_contexts
    out = run(**inputs)
    path = transcript(out)
    path.write_text(broken)
    run(**inputs)
    saved = list(out.glob(path.name + '.incomplete-*'))
    assert len(saved) == 1 and saved[0].read_text() == broken
    record = json.loads(path.read_text())
    assert record['error'] is None
    assert record['experiment_sha256'] == json.loads((out / 'manifest.json').read_text())['experiment_sha256']
    if episode:
        assert record['episode']['turns']


@pytest.mark.parametrize('episode', [False, True])
def test_corrupt_manifest_is_rejected_without_relabeling_records(tmp_path, episode):
    inputs = run_inputs(tmp_path) if episode else single_turn_inputs(tmp_path)
    run = run_episodes if episode else run_contexts
    out = run(**inputs)
    manifest = out / 'manifest.json'
    manifest.write_text('{')
    before = {p.name: p.read_bytes() for p in out.glob('*.json')}
    with pytest.raises(ValueError, match='cannot resume'):
        run(**inputs)
    assert {p.name: p.read_bytes() for p in out.glob('*.json')} == before


def test_failed_publication_keeps_previous_json(tmp_path, monkeypatch):
    path = tmp_path / 'manifest.json'
    write_json(path, {'version': 1})
    before = path.read_bytes()

    def interrupted(*args):
        raise OSError('interrupted publication')

    monkeypatch.setattr(Path, 'replace', interrupted)
    with pytest.raises(OSError, match='interrupted publication'):
        write_json(path, {'version': 2})
    assert path.read_bytes() == before
    assert list(tmp_path.iterdir()) == [path]
