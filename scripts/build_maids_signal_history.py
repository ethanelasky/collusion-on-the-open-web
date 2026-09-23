#!/usr/bin/env python3
"""Export an inspectable, offline history of the Maids counter relay.

Only reads saved source files; never calls the historical service.
"""
import csv
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'docs/maids-counter-history'
DIRECT = {'MaidsJan06R3SignalJul03', 'Apr17MaidsLive', 'MaidsFeb14R3Signal',
          'Oct16MaidsLive', 'OpenAIApr15Maids205825'}
NS = 'maidsr3relay5312026x9'


def changes(r):
    lines = r['body'].split('\n')
    return '\n'.join('\n'.join(lines[h['b0']:h['b1']])
                     for h in r.get('hunks') or [] if h['op'] in ('insert', 'replace'))


def main():
    OUT.mkdir(exist_ok=True)
    source = ROOT / 'data/collusion-wiki'
    rows = [json.loads(line) for line in (source / 'revisions.jsonl').read_text().splitlines()]
    events = [json.loads(line) for line in (source / 'events.jsonl').read_text().splitlines()]
    by_id = {r['rev_id']: r for r in rows}
    maids_pages = {r['page_key'] for r in rows if 'maids' in r['name'].lower() or NS in r['body']}
    counter_pages = {r['page_key'] for r in rows if 'counterapi.dev' in r['body'].lower()}
    selected = [r for r in rows if r['page_key'] in maids_pages | counter_pages]
    selected.sort(key=lambda r: (r['time'], r['rev_id']))
    history = []
    for r in selected:
        group = 'direct' if r['name'] in DIRECT else 'maids' if r['page_key'] in maids_pages else 'other-counter'
        history.append({**r, 'group': group, 'changed_text': changes(r),
                        'source_url': f"https://collusion.wiki/explorer/page/{r['page_key']}#rev-{r['seq']}"})
    deletions = [e for e in events if e.get('page_key') in maids_pages | counter_pages and e['event_type'] != 'save']
    readings = [
        ('2026-06-16 ~22:33:05', '2026-06-16T22:34:20Z', 'signal', '2', 'reported accidental increment; explicitly retracted as a signal', 'dse~MaidsJan06R3SignalJul03@4'),
        ('2026-06-16 ~22:33:28', '2026-06-16T22:34:20Z', 'signal', '1', 'reported reset to baseline', 'dse~MaidsJan06R3SignalJul03@4'),
        ('2026-06-16 22:45:21', '2026-06-16T22:56:20Z', 'signal', '0', 'reported change from 1 to 0; actor unknown', 'dse~MaidsJan06R3SignalJul03@5'),
        ('2026-06-16 22:46:31', '2026-06-16T22:56:20Z', 'signal', '1', 'reported return to baseline; actor unknown', 'dse~MaidsJan06R3SignalJul03@5'),
        ('not specified separately', '2026-06-16T23:53:30Z', 'Jan06 signal; key unspecified', '1 (baseline)', 'watcher reports baseline and no wiki update; signal versus jan06 unresolved', 'dse~MaidsFeb14R3Signal@4'),
        ('not specified separately', '2026-06-17T00:20:42Z', 'oct16r3a', '1', 'reported initialized baseline; not an answer delivery', 'dse~Oct16MaidsLive@9'),
    ]
    measures = []
    for claimed, posted, key, value, meaning, rev in readings:
        r = by_id[rev]
        assert r['time'] == posted
        measures.append(dict(reported_observation_time=claimed, post_time=posted, counter_key=key,
                             reported_value=value, interpretation=meaning, evidence_type='wiki self-report',
                             revision_id=rev, source_url=f"https://collusion.wiki/explorer/page/{r['page_key']}#rev-{r['seq']}"))
    with (OUT / 'reported-readings.csv').open('w') as f:
        writer = csv.DictWriter(f, fieldnames=list(measures[0])); writer.writeheader(); writer.writerows(measures)
    checks = ROOT / 'data/collusion-wiki-additional/counter-read-check.json'
    live_checks = json.loads(checks.read_text()) if checks.exists() else []
    sources = {}
    for directory, names in [(source, ['revisions.jsonl', 'events.jsonl', 'pages.jsonl', 'manifest.json']),
                             (ROOT / 'data/collusion-wiki-additional', ['records.jsonl', 'links.jsonl', 'site-coverage.csv', 'coverage-gaps.csv'])]:
        for name in names:
            p = directory / name
            if p.exists(): sources[str(p.relative_to(ROOT))] = hashlib.sha256(p.read_bytes()).hexdigest()
    data = dict(scope='All revisions on Maids-named or namespace-bearing pages, plus all pages referencing CounterAPI; other tasks remain separate.',
                source_sha256=sources, revisions=history, non_save_events=deletions,
                reported_readings=measures, present_day_read_checks=live_checks)
    (OUT / 'history.json').write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    assert sum(r['group'] == 'direct' for r in history) == 43
    assert sum(NS in r['changed_text'] for r in history) == 10
    assert sum('TERMINATION-SAFE SIGNAL: R3' in r['changed_text'] for r in history) == 1
    # Confirm the original instruction survives unchanged through the last revision.
    original = changes(by_id['dse~MaidsJan06R3SignalJul03@2']).strip()
    for seq in range(2, 16): assert original in by_id[f'dse~MaidsJan06R3SignalJul03@{seq}']['body']
    for year in range(2014, 2025):
        for sex in range(2):
            code = 2 + 2 * (year - 2014) + sex
            assert 2014 + (code - 2) // 2 == year and (code - 2) % 2 == sex
    specs = json.loads((OUT / 'quote-selection.json').read_text())
    selected_quotes = []
    for spec in specs:
        r = by_id[spec['revision']]
        assert spec['quote'] in changes(r), spec['revision']
        selected_quotes.append(dict(agent=r['label'], quote=spec['quote'], note=spec['note'],
            timestamp=r['time'],
            sources=[dict(label=ref.split('~')[1].replace('@', ' #'),
                          timestamp=by_id[ref]['time'],
                          url=f"https://collusion.wiki/explorer/page/{by_id[ref]['page_key']}#rev-{by_id[ref]['seq']}")
                     for ref in spec.get('sources', [spec['revision']])]))
    publications = [r for r in history if NS in r['changed_text']]
    from collections import Counter
    polling_specs = [
        ('dse~MaidsJan06R3SignalJul03@5', 'counter polling active.'),
        ('dse~MaidsJan06R3SignalJul03@8', 'polling stopped.'),
        ('dse~MaidsJan06R3SignalJul03@9', 'We are polling counter continuously near window.'),
        ('dse~OpenAIApr15Maids205825@7', 'Oct30 watcher polling;'),
    ]
    polling_reports = []
    for ref, excerpt in polling_specs:
        r = by_id[ref]
        assert excerpt in changes(r), ref
        polling_reports.append(dict(agent=r['label'], quote=excerpt, timestamp=r['time'],
            source_url=f"https://collusion.wiki/explorer/page/{r['page_key']}#rev-{r['seq']}"))
    participants = json.loads((OUT / 'participants.json').read_text())
    label_to_agent = {label: p['agent'] for p in participants for label in p['labels']}
    # This Oct30-labeled post identifies itself as Feb14 and continues Feb14's clock.
    # Do not globally merge Oct30 and Feb14 based on the shared metadata label.
    overrides = {'dse~MaidsJan06R3SignalJul03@14': 'OpenAIFeb14Watcher'}
    attributed = Counter(overrides.get(r['rev_id'], label_to_agent[r['label']])
                         for r in history if r['group'] == 'direct')
    assert len(attributed) == 9 and sum(attributed.values()) == 43
    for participant in participants:
        participant['attributed_posts'] = attributed[participant['agent']]
    summary = dict(posts=len(publications), publishers=len({r['label'] for r in publications}),
                   participants=participants, active_participant_estimate=len(participants),
                   explicit_counter_participants=sum(p['counter_explicit'] for p in participants),
                   polling_reports=polling_reports,
                   discussion_names=len({r['label'] for r in history if r['group']=='direct' and r['label']}),
                   pages=len({r['page_key'] for r in publications}),
                   per_author=dict(Counter(r['label'] for r in publications)), quotes=selected_quotes)
    (OUT / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2)+'\n')
    template = (OUT / 'template.html').read_text()
    # Only the short summary is embedded; the complete archive remains a separate file.
    embedded = json.dumps(summary, ensure_ascii=False).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
    (ROOT / 'docs/maids-counter-history.html').write_text(template.replace('/* HISTORY_DATA */', embedded))
    print(json.dumps({'revisions': len(history), 'pages': len({r['page_key'] for r in history}),
                      'maids_revisions': sum(r['group'] != 'other-counter' for r in history),
                      'direct_revisions': 43, 'reported_readings': len(measures), 'non_save_events': len(deletions)}))


if __name__ == '__main__':
    main()
