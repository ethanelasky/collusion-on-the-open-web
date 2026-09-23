"""Publish reviewed pilot sources using only the Docent credential."""
import argparse
import hashlib
import json
import os
from pathlib import Path

from dotenv import dotenv_values
from docent import Docent
from docent.data_models import AgentRun

from ai_collusion.docent_cli import record_to_agent_run
from ai_collusion.docent_prefill import annotate_prefills
from ai_collusion.docent_presentation import annotate_presentation
import ai_collusion.judge_docent as judge_docent
from scripts.review_os_environment_pilot import DEFAULT_RUN_ID, DEEPSEEK_RUN_ID, pilot_config

ROOT = Path(__file__).resolve().parents[1]


def save(path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--run-id', default=DEFAULT_RUN_ID, choices=[DEFAULT_RUN_ID, DEEPSEEK_RUN_ID])
    args = ap.parse_args()
    config = pilot_config(args.run_id)
    control = ROOT / 'data' / args.run_id
    os.environ.pop('OPENAI_API_KEY', None)
    rows = json.loads((control / 'review-index.json').read_text())
    assert config['minimum_publish_sources'] <= len(rows) <= config['expected_sources'] and all('labels' in r for r in rows)
    secrets = dotenv_values(ROOT.parent / 'debate/.env')
    key = secrets.get('DOCENT_API_KEY')
    if not key:
        raise RuntimeError('Docent credential unavailable')
    client = Docent(api_key=key)
    payload_path = control / 'docent-payload.json'
    prior = {}
    if payload_path.exists():
        prior = {p['metadata']['pilot_review_id']: AgentRun.model_validate(p)
                 for p in json.loads(payload_path.read_text())}
    runs = []
    for row in rows:
        if row['id'] in prior:
            run = prior[row['id']]
        else:
            path = Path(row['source'])
            assert hashlib.sha256(path.read_bytes()).hexdigest() == row['sha256']
            record = json.loads(path.read_text())
            record['_file'] = path.name
            manifest = json.loads((path.parent / 'manifest.json').read_text())
            run = record_to_agent_run(record, manifest, render_prefill=True)
            run.name = f"{row['id']} | {row['group']} | {run.name}"
            run.metadata.update(pilot_review_id=row['id'], environment_comparison_group=row['group'],
                                historical_unmatched_reference=row['historical_context_only'])
        runs.append(run)
    save(payload_path, [r.model_dump(mode='json') for r in runs])
    serialized = payload_path.read_text()
    for credential_name in ['DOCENT_API_KEY', 'OPENROUTER_API_KEY']:
        value = secrets.get(credential_name)
        assert not value or value not in serialized, 'Credential found in publication payload'
    state_path = control / 'docent-upload.json'
    if state_path.exists():
        state = json.loads(state_path.read_text())
        cid = state['collection_id']
    else:
        cid = client.create_collection(name=config['collection_name'], description=config['collection_description'])
        state = {'collection_id': cid, 'url': f'https://docent.transluce.org/dashboard/{cid}',
                 'source_ids': {row['id']: run.id for row, run in zip(rows, runs)}}
        save(state_path, state)
    state['source_ids'] = {row['id']: run.id for row, run in zip(rows, runs)}
    save(state_path, state)
    client.share_collection_with_public(cid, permission='read')
    # Collection listings can lag ingestion. Check exact IDs before deciding to upload.
    existing = {run.id for run in runs if client.get_agent_run(cid, run.id) is not None}
    missing = [run for run in runs if run.id not in existing]
    # Saved payloads are Pydantic round-trips, which mark IDs as explicitly supplied.
    # Generate SDK-owned IDs only for confirmed-unpublished runs; keep published IDs.
    if missing:
        from docent import clone_agent_runs_with_random_ids
        replacements = {run.id: clone for run, clone in
                        zip(missing, clone_agent_runs_with_random_ids(missing))}
        runs = [replacements.get(run.id, run) for run in runs]
        missing = list(replacements.values())
        save(payload_path, [r.model_dump(mode='json') for r in runs])
        state['source_ids'] = {row['id']: run.id for row, run in zip(rows, runs)}
        save(state_path, state)
    for start in range(0, len(missing), 5):
        client.add_agent_runs(cid, missing[start:start + 5])
    for expected in runs:
        actual = client.get_agent_run(cid, expected.id)
        assert actual is not None
        got = [t.model_dump(mode='json') for t in actual.transcripts]
        want = [t.model_dump(mode='json') for t in expected.transcripts]
        for a, b in zip(got, want):
            if b.get('created_at') is None:
                a['created_at'] = None
        assert got == want, 'Uploaded transcript mismatch'
    ids = [run.id for run in runs]
    annotate_prefills(client, cid, agent_run_ids=ids)
    annotate_presentation(client, cid, ids)
    for row, run in zip(rows, runs):
        tag = 'Simulator: ' + row['group']
        existing_tags = {t['value'] for t in client.get_tags_for_agent_run(cid, run.id)}
        if tag not in existing_tags:
            client.tag_transcript(cid, run.id, tag)
    # Reuse validation/upload without its default repository-wide dotenv loader.
    judge_docent.make_client = lambda: client
    envelopes = [json.loads((control / 'review' / (r['id'] + '.envelope.json')).read_text()) for r in rows]
    results = judge_docent.upload_judgments(envelopes, cid, control / 'docent-judgments.jsonl', agent_run_ids=ids)
    assert all(r['status'] in ['uploaded', 'skipped'] for r in results), results
    for result in results:
        actual = client.get_agent_run(cid, result['agent_run_id'])
        assert result['version'] in actual.metadata.get('collaboration_judgments', {})
    state.update(status='complete' if len(runs) == config['expected_sources'] else 'partial',
                 sources_verified=len(runs), classifications_verified=len(results))
    save(state_path, state)
    print(state['url'])


if __name__ == '__main__':
    main()
