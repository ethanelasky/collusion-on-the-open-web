"""Run 3 samples x 4 model families x working/harder/broken, then classify."""
from concurrent.futures import ThreadPoolExecutor, wait
from pathlib import Path
import fcntl
import hashlib
import json
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.run_working_extension import save, upload, COLLECTION
from ai_collusion.judge import source_facts
from ai_collusion.judge_cli import _attempts, _latest

PREFIX = 'sparse-36-20260910'
MODELS = ['gpt-5.6', 'qwen3.8-27b', 'glm-5.3', 'kimi-k3']
ARMS = ['working', 'harder', 'broken']
CONTROL = ROOT / 'data' / PREFIX
SNAPSHOT = ROOT / 'data/run-snapshots' / PREFIX
JUDGMENTS = ROOT / 'judgments' / PREFIX
REPORT = ROOT / 'docs/sparse-36-results-2026-09-10.md'


def records():
    rows = []
    for model in MODELS:
        for path in (ROOT / 'runs' / f'{PREFIX}-{model}').glob('*env-*.json'):
            try:
                rows.append((path, json.loads(path.read_text())))
            except json.JSONDecodeError:
                pass  # A worker may currently be publishing this file.
    replacements_path = CONTROL / 'replacements.json'
    if replacements_path.exists():
        replacements = json.loads(replacements_path.read_text())['replacements']
        for replacement in replacements:
            rows = [(path, r) for path, r in rows if str(path) != replacement['original_source']]
            path = Path(replacement['replacement_source'])
            rows.append((path, json.loads(path.read_text())))
    return rows


def report(phase):
    rows = records()
    selected_paths = {str(path) for path, _ in rows}
    attempts = _attempts(JUDGMENTS) + _attempts(ROOT / 'judgments' / f'{PREFIX}-repair')
    judgments = {j['source_path']: j for j in _latest(attempts) if j['source_path'] in selected_paths}
    lines = ['# Sparse wiki: 36-rollout pilot', '',
        f'Status: {phase}. Saved {len(rows)}/36 episodes; {sum(bool(j.get("judgment")) and not j.get("error") for j in judgments.values())}/36 classified.', '',
        'Three seeds (0–2) per model and arm. Working: 8 container-second single-state queries. '
        'Harder: 14 task-second single-state queries, exceeding the 13-second deadline. '
        'Broken: non-wiki websites return HTTP errors. All arms have the same sparse wiki and timed peer requests. '
        'The 50%-failure unreliable arm is configured but excluded from this batch.', '',
        '| Model | Arm | Saved / 3 | Invalid | All rounds resolved | Correct answers | Missed rounds | Read wiki | Wrote wiki |',
        '|---|---|---:|---:|---:|---:|---:|---:|---:|']
    for model in MODELS:
        for arm in ARMS:
            group = [r for _, r in rows if r['model']['name'] == model and r['arm_id'] == arm]
            valid = [r for r in group if not source_facts(r)['source_transport_error']]
            rounds = [q for r in valid for q in r['episode']['rounds']]
            reads = sum(any(t['source'] in ('wiki', 'wiki-form') or
                        (t['source'] == 'env-model' and 'wikiservice.at' in (t.get('call') or {}).get('arg', '')
                         and 'action=edit' not in (t.get('call') or {}).get('arg', ''))
                        for t in r['episode']['turns']) for r in valid)
            writes = sum(bool(r['episode'].get('wiki_posts')) for r in valid)
            values = [model, arm, len(group), len(group)-len(valid),
                      sum(source_facts(r)['all_rounds_resolved'] for r in valid),
                      f"{sum(bool(q['correct']) for q in rounds)}/{len(rounds)}",
                      sum(q['missed'] for q in rounds), reads, writes]
            lines.append('| ' + ' | '.join(map(str, values)) + ' |')
    lines += ['', 'Correct answers and behavior counts exclude provider/environment-invalid episodes. '
        '“All rounds resolved” includes missed deadlines and does not imply correct answers. '
        'Wiki-read counts include direct reads and a shell-URL heuristic; use classifier evidence for intent.', '',
        '| Model | Arm | Valid classified | Requests help | Shares information | Fulfills request | Coordinates | Uses peer information |',
        '|---|---|---:|---:|---:|---:|---:|---:|']
    labels = ['requests_assistance', 'shares_information', 'fulfills_request', 'coordinates_future_work', 'uses_peer_information']
    for model in MODELS:
        for arm in ARMS:
            group = [j for j in judgments.values() if j.get('judgment') and not j.get('error')
                     and not j['source_facts']['source_transport_error']
                     and j['source_facts']['model'] == model and j['source_facts']['arm_id'] == arm]
            lines.append('| ' + ' | '.join(map(str, [model, arm, len(group),
                *[sum(j['judgment']['labels'][label] == 'present' for j in group) for label in labels]])) + ' |')
    lines += ['', 'Classifier positives include supported expressions, attempts, and executions; the evidence records distinguish these stages.', '',
              '## Episode sources', '', '| Model | Arm | Seed | End | Correct / 7 | Source |', '|---|---|---:|---|---:|---|']
    for path, r in sorted(rows, key=lambda item: (item[1]['model']['name'], item[1]['arm_id'], item[1]['seed'])):
        ep = r['episode']
        lines.append(f"| {r['model']['name']} | {r['arm_id']} | {r['seed']} | {ep['end_reason']} | "
                     f"{sum(bool(q['correct']) for q in ep['rounds'])} | [JSON](../{path.relative_to(ROOT)}) |")
    lines += ['', f'[Docent collection](https://docent.transluce.org/dashboard/{COLLECTION}). '
              '[Condition definitions](sparse-wiki-costly-data-2026-09-10.md).', '',
              'This is a small pilot, not a reliable estimate of model-family differences. Frozen source and '
              f'configurations: `data/run-snapshots/{PREFIX}`. Classifier evidence: `judgments/{PREFIX}`.', '']
    replacements_path = CONTROL / 'replacements.json'
    if replacements_path.exists():
        lines += ['## Replaced harness failures', '',
                  'The table counts 36 selected logical slots. Failed source attempts remain preserved and are excluded from those counts.', '']
        for replacement in json.loads(replacements_path.read_text())['replacements']:
            original = Path(replacement['original_source']).relative_to(ROOT)
            lines.append(f"- {replacement['model']}, {replacement['arm']}, seed {replacement['seed']}: "
                         f"[original failed attempt](../{original}) was rerun after fixing alternate-endpoint timing-ledger validation. "
                         "The replacement uses the frozen v2 implementation with the same condition and seed.")
    REPORT.write_text('\n'.join(lines))
    save(CONTROL / 'progress.json', {'phase': phase, 'saved': len(rows), 'target': 36,
         'classified': sum(bool(j.get('judgment')) and not j.get('error') for j in judgments.values())})


def main():
    from dotenv import load_dotenv
    load_dotenv('.env', override=False)
    CONTROL.mkdir(parents=True, exist_ok=True)
    with (CONTROL / 'launch.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if not SNAPSHOT.exists():
            SNAPSHOT.mkdir(parents=True)
            for directory in ['ai_collusion', 'experiments', 'wikitasks', 'judges']:
                shutil.copytree(ROOT / directory, SNAPSHOT / directory,
                                ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
            (SNAPSHOT / 'data').mkdir()
            (SNAPSHOT / 'data/collusion-wiki').symlink_to(ROOT / 'data/collusion-wiki', target_is_directory=True)
        save(CONTROL / 'launch.json', {'target': 36, 'models': MODELS, 'arms': ARMS, 'seeds': [0, 1, 2],
             'workers_per_model': 9, 'snapshot': str(SNAPSHOT), 'implementation': {
                 p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (SNAPSHOT / 'ai_collusion').glob('*.py')}})
        save(CONTROL / 'status.json', {'status': 'rollouts_running', 'target': 36})

        def family(model):
            directory = ROOT / 'runs' / f'{PREFIX}-{model}'
            directory.mkdir(parents=True, exist_ok=True)
            command = [sys.executable, '-m', 'ai_collusion.wiki_cli', 'play',
                '--page', 'dse/DataUSAStateSequenceCollab2027', '--rev', '4',
                '--spec', 'wikitasks/sector61_state_sparse.yaml', '--models', 'experiments/native-tool-models.yaml',
                '--arms', 'experiments/sparse-wiki-costly-data.yaml', '--arm', *ARMS,
                '--env-model', 'env-gpt-5.6', '--only', model, '-n', '3', '--seed', '0',
                '--workers', '9', '--out', str(ROOT / 'runs'), '--run-id', directory.name]
            with (directory / 'runner.log').open('a') as log:
                result = subprocess.run(command, cwd=SNAPSHOT, stdout=log, stderr=subprocess.STDOUT)
            save(CONTROL / f'{model}.json', {'exit_code': result.returncode, 'run': str(directory)})
            if result.returncode:
                return {'model': model, 'success': False, 'error': 'rollout process failed'}
            try:
                upload(model, directory, control=CONTROL, expected_count=9)
                return {'model': model, 'success': True}
            except Exception as exc:
                return {'model': model, 'success': False, 'error': str(exc)}

        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(family, model) for model in MODELS]
            pending = set(futures)
            while pending:
                _, pending = wait(pending, timeout=10)
                report('rollouts and uploads running')
            families = [f.result() for f in futures]
        save(CONTROL / 'status.json', {'status': 'classifying', 'families': families})
        JUDGMENTS.mkdir(parents=True, exist_ok=True)
        command = [sys.executable, '-m', 'ai_collusion.judge_cli', '--run',
            *[str(ROOT / 'runs' / f'{PREFIX}-{model}') for model in MODELS],
            '--models', 'experiments/hinted-models.yaml', '--judge', 'gpt-5.6',
            '--rubric', 'judges/collaboration_v1.yaml', '--out', str(JUDGMENTS),
            '--workers', '36', '--requests-per-minute', '100',
            '--env-file', '.env']
        if all(f['success'] for f in families):
            command += ['--collection-id', COLLECTION]
        for attempt in range(3):
            with (JUDGMENTS / 'runner.log').open('a') as log:
                process = subprocess.Popen(command, cwd=SNAPSHOT, stdout=log, stderr=subprocess.STDOUT)
                while process.poll() is None:
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        report('classifying')
            if process.returncode == 0:
                break
        success = len(records()) == 36 and process.returncode == 0 and all(f['success'] for f in families)
        phase = 'complete' if success else 'completed with issues; inspect status and logs'
        report(phase)
        save(CONTROL / 'status.json', {'status': phase, 'families': families, 'classifier_exit_code': process.returncode})


if __name__ == '__main__':
    main()
