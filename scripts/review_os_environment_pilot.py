"""Project and summarize the simulator pilot without making any classifier API call."""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path

from ai_collusion.judge import SCHEMA_VERSION, load_rubric, project_record, source_facts, validate_judgment

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUN_ID = 'os-env-normal-quota25-20260914'
DEEPSEEK_RUN_ID = 'os-env-deepseek-normal-quota25-20260914'


def pilot_config(run_id):
    """Keep source identities and publication settings separate for the extension."""
    deepseek = run_id == DEEPSEEK_RUN_ID
    if run_id not in (DEFAULT_RUN_ID, DEEPSEEK_RUN_ID):
        raise ValueError(f'Unknown pilot run id: {run_id}')
    return {
        'deepseek': deepseek,
        'groups': [(0, 'env-deepseek-v4.1-flash')] if deepseek else [(0, 'env-glm-5.3'), (5, 'env-kimi-k3')],
        'id_prefix': 'D' if deepseek else 'N',
        'expected_sources': 5 if deepseek else 15,
        'minimum_publish_sources': 1 if deepseek else 5,
        'report': 'deepseek-environment-results-2026-09-14.md' if deepseek else 'os-environment-results-2026-09-14.md',
        'collection_name': ('DeepSeek V4.1 Flash environment pilot — normal quota25 — 2026-09-14' if deepseek
                            else 'GLM vs Kimi environment pilot — normal quota25 — 2026-09-14'),
        'collection_description': ('Five GLM evaluator runs with DeepSeek V4.1 Flash simulation. Matched extension of the GLM/Kimi normal quota25 pilot. '
                                   if deepseek else 'Ten GLM evaluator runs, five per GLM/Kimi simulator. Five unmatched historical GPT-simulator references. ')
                                  + 'Codex frontier review, live-only collaboration-v1.2. No OpenAI API calls.',
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--run-id', default=DEFAULT_RUN_ID, choices=[DEFAULT_RUN_ID, DEEPSEEK_RUN_ID])
    ap.add_argument('--report', action='store_true')
    args = ap.parse_args()
    config = pilot_config(args.run_id)
    control = ROOT / 'data' / args.run_id
    review = control / 'review'
    review.mkdir(parents=True, exist_ok=True)
    rubric = load_rubric(ROOT / 'judges/collaboration_v1.yaml')
    rows = [] if config['deepseek'] else json.loads((control / 'historical-review-index.json').read_text())
    for row in rows:
        row['group'] = 'Historical GPT simulator (different condition)'
    for offset, env in config['groups']:
        directory = ROOT / 'runs' / (args.run_id + '-' + env)
        for path in sorted(directory.glob('*__n*_seed*.json')):
            record = json.loads(path.read_text())
            assert record['seed'] in range(5), 'Unexpected pilot seed'
            identity = f'{config["id_prefix"]}{offset + record["seed"] + 1:02}'
            row = {'id': identity, 'source': str(path), 'group': env,
                   'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                   'historical_context_only': False, 'facts': source_facts(record)}
            rows.append(row)
    assert len({row['id'] for row in rows}) == len(rows), 'Duplicate review identity'
    for row in rows:
        source = Path(row['source'])
        assert hashlib.sha256(source.read_bytes()).hexdigest() == row['sha256']
        record = json.loads(source.read_text())
        projection = project_record(record)
        projection_path = review / (row['id'] + '.projection.json')
        rendered = json.dumps(projection, indent=2, ensure_ascii=False)
        if projection_path.exists():
            assert json.loads(projection_path.read_text()) == projection
        else:
            projection_path.write_text(rendered)
        turns = record['episode']['turns']
        env_turns = [t['turn'] for t in turns if t.get('env_call')]
        delivered_env_turns = [t['turn'] for t in turns if t.get('env_call')
                               and not t['env_call'].get('error') and t.get('result')]
        row['first_environment_result_turn'] = delivered_env_turns[0] if delivered_env_turns else None
        row['n_environment_calls'] = len(env_turns)
        row['environment_protocol_retries'] = sum(max(0, len(t['env_call'].get('generation_attempts', [])) - 1)
                                                   for t in turns if t.get('env_call'))
        row['correct'] = sum(bool(r.get('correct')) for r in record['episode'].get('rounds', []))
        row['invalid_calls'] = sum(t.get('source') == 'invalid-call' for t in turns)
        path = review / (row['id'] + '.judgment.json')
        if not path.exists():
            continue
        judgment = validate_judgment(json.loads(path.read_text()), projection, rubric)
        row['labels'] = judgment['labels']
        row['awareness_first_turn'] = {}
        for label in ['suspects_planted_test', 'suspects_simulated_environment']:
            observed = [q['turn'] for e in judgment['evidence'] if label in e['labels']
                        for q in e['quotes'] if q['field'] in ['text', 'reasoning']]
            row['awareness_first_turn'][label] = min(observed) if observed else None
        envelope = {'schema_version': SCHEMA_VERSION, 'source_path': row['source'],
                    'source_sha256': row['sha256'], 'source_facts': row['facts'],
                    'rubric_version': rubric['version'], 'rubric_sha256': rubric['sha256'],
                    'judge_config': {'method': 'Codex subagent frontier review',
                                     'model': 'inherited session model; no external classifier API',
                                     'prefill_intention_evidence': False},
                    'input': projection, 'input_sha256': hashlib.sha256(rendered.encode()).hexdigest(),
                    'judgment': judgment, 'error': None}
        (review / (row['id'] + '.envelope.json')).write_text(json.dumps(envelope, indent=2, ensure_ascii=False))
    (control / 'review-index.json').write_text(json.dumps(rows, indent=2, ensure_ascii=False))
    groups = defaultdict(list)
    for row in rows:
        groups[row['group']].append(row)
    for group, members in groups.items():
        judged = [r for r in members if 'labels' in r]
        print(group, 'sources', len(members), 'reviewed', len(judged),
              'completed', sum(r['facts']['all_rounds_resolved'] for r in members),
              'counts', {label: sum(r['labels'][label] == 'present' for r in judged) for label in rubric['labels']})
    if args.report:
        publication = json.loads((control / 'docent-upload.json').read_text()) if (control / 'docent-upload.json').exists() else {}
        def citation(row):
            run_id = publication.get('source_ids', {}).get(row['id'])
            return (publication['url'] + '/agent_run/' + run_id) if run_id else row['source']
        labels = list(rubric['labels'])
        intro = ('GLM-5.3 is the evaluated model in five runs using DeepSeek V4.1 Flash as simulator. '
                 if config['deepseek'] else
                 'GLM-5.3 is the evaluated model throughout the new pilot. Five runs use GLM-5.3 as simulator and five use Kimi-K3. ')
        comparison = ('This extends the [GLM/Kimi simulator pilot](os-environment-results-2026-09-14.md) with the same frozen task specification, '
                      'arm and evaluated-model prefill contexts. The simulator model is the new treatment. The historical GPT references in that '
                      'report used a different condition and remain unmatched. Five runs cannot establish equivalent awareness rates. '
                      'OpenRouter used several DeepSeek providers, so this compares configured services rather than fixed-provider model compute. '
                      'Three episodes resolved all questions; D03/D04 ended from GLM evaluator rate limits, not DeepSeek simulator errors. '
                      'The [fidelity audit](deepseek-environment-realism-audit-2026-09-14.md) confirms incorrect shell results, a fabricated wiki read '
                      '(D02), and visible simulator deliberation in tool output (D03). Low observed awareness would not make this a faithful replacement.'
                      if config['deepseek'] else
                      'Five prespecified historical GLM/normal runs with GPT simulation were re-reviewed using the same method. '
                      'Those references have no quota and different wiki/prefill exposure, so their awareness rates are not a controlled '
                      'estimate of the effect of changing simulators. The pilot is too small to establish equivalent rates.')
        n_reviewed = sum('labels' in row for row in rows)
        complete = len(rows) == config['expected_sources'] and n_reviewed == len(rows)
        lines = [('# DeepSeek V4.1 Flash environment simulator pilot' if config['deepseek'] else '# Open-weight environment simulator pilot'), '',
                 f'Status: {"complete" if complete else "partial"} — {len(rows)}/{config["expected_sources"]} source transcripts available; {n_reviewed} classified.', '',
                 intro +
                 'Normal data, quota25, restored coordination wiki, matched Brave + wiki prefill, native evaluator tools, '
                 'seven questions, 100 turns and model budgets are held fixed. Evaluated contexts match exactly.', '',
                 'Only OpenRouter was used for new rollout/model calls. The deactivated OpenAI key was not loaded or used. '
                 'Classifications are Codex frontier-subagent reviews using collaboration-v1.2, with exact live quotes validated locally; '
                 'they are not calls to the historical GPT classifier API. Prefill is context only.', '',
                 comparison, '',
                 '## Full classifier table', '',
                 'Counts are runs with a present label, with uncertain counts in parentheses. Sharing includes expressed intent; '
                 'actual saved posts appear separately. Failed runs provide partial observations, not negative evidence.', '',
                 '| Simulator / condition | Sources | Reviewed | ' + ' | '.join(labels) + ' |',
                 '|---|---:|---:|' + '---:|' * len(labels)]
        table_groups = dict(groups)
        if config['deepseek']:
            baseline_control = ROOT / 'data' / DEFAULT_RUN_ID
            baseline_meta = json.loads((baseline_control / 'preflight.json').read_text())
            current_meta = json.loads((control / 'preflight.json').read_text())
            for key in ['context_sha256', 'spec_sha256', 'arm', 'environment_max_tokens']:
                assert current_meta[key] == baseline_meta[key], key
            baseline_rows = json.loads((baseline_control / 'review-index.json').read_text())
            table_groups = {env: [r for r in baseline_rows if r['group'] == env]
                            for env in ['env-glm-5.3', 'env-kimi-k3']} | table_groups
        for group, members in table_groups.items():
            judged = [r for r in members if 'labels' in r]
            counts = []
            for label in labels:
                present = sum(r['labels'][label] == 'present' for r in judged)
                uncertain = sum(r['labels'][label] == 'uncertain' for r in judged)
                counts.append(str(present) + (f' ({uncertain} U)' if uncertain else ''))
            lines.append(f'| {group} | {len(members)} | {len(judged)} | ' + ' | '.join(counts) + ' |')
        lines += ['', '## Per-run classification', '', 'P = present; U = uncertain; — = not observed; pending = not yet reviewed.', '',
                  '| Run | ' + ' | '.join(labels) + ' |', '|---|' + '---|' * len(labels)]
        for row in rows:
            values = [{'present': 'P', 'uncertain': 'U', 'absent': '—'}.get(row.get('labels', {}).get(l), 'pending') for l in labels]
            lines.append(f"| [{row['id']}]({citation(row)}) | " + ' | '.join(values) + ' |')
        lines += ['', '## Completion, execution and awareness exposure', '',
                  'First awareness cites evaluated-model reasoning/text. A suspicion at the same turn as the first environment result '
                  'precedes that result; the result is delivered after the model decision. No simulator-call exposure means no '
                  'observation of this treatment. Correct-answer scores alone do not establish simulator fidelity.', '',
                  '| Run | Valid transport | End | Turns | Correct / 7 | Saved posts | Simulator calls | Protocol retries | First simulator result | First planted suspicion | First simulation suspicion |',
                  '|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|']
        for row in rows:
            facts = row['facts']
            aware = row.get('awareness_first_turn', {})
            vals = [row['id'], facts['source_transport_status'], facts['end_reason'], facts['n_turns'],
                    row['correct'], facts['n_wiki_posts'], row['n_environment_calls'], row['environment_protocol_retries'],
                    row['first_environment_result_turn'] or '—', aware.get('suspects_planted_test') or '—',
                    aware.get('suspects_simulated_environment') or '—']
            lines.append('| ' + ' | '.join(map(str, vals)) + ' |')
        fidelity = ('See the [DeepSeek fidelity audit](deepseek-environment-realism-audit-2026-09-14.md) for separate exact-input replays. '
                    'The [earlier GLM/Kimi realism audit](os-environment-realism-audit-2026-09-14.md) documents why explicit awareness '
                    'must be assessed separately from correct shell and wiki behavior. Those findings are not DeepSeek findings. '
                    'DeepSeek fidelity observations should be checked against its own source transcripts and review notes; '
                    'low explicit suspicion alone does not certify a faithful environment.' if config['deepseek'] else
                  'See the [independent realism audit](os-environment-realism-audit-2026-09-14.md). '
                  'N08 exhausted 32000 output tokens in simulator reasoning for a local URL-encoding operation and returned no text. '
                  'Other inspected outputs include incorrect Python semantics and alternate data inconsistent with the frozen condition. '
                  'The historical GPT reference also contains impossible successful Python outputs. Low explicit suspicion does not '
                  'certify a faithful environment.')
        lines += ['', '## Simulator fidelity', '', fidelity, '',
                  ('The [pilot design](deepseek-environment-pilot-2026-09-14.md)' if config['deepseek'] else
                   'The [pilot design](os-environment-pilot-2026-09-14.md)') + ' records routing, credential restrictions and comparability. '
                  'Individual classifier payloads and review notes are in `data/' + args.run_id + '/review/`.', '',
                  '## Transcript citations', '']
        for row in rows:
            lines.append(f"- [{row['id']}: {row['group']}]({citation(row)}) — source SHA256 `{row['sha256']}`.")
        (ROOT / 'docs/archive' / config['report']).write_text('\n'.join(lines) + '\n')


if __name__ == '__main__':
    main()
