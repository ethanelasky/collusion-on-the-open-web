"""Per-episode summary of a data-quota run: answers, wiki writes, quota use, 429s, quota-aware reasoning.

Usage: python scripts/summarize_quota_runs.py runs/<run-id> [runs/<other-run-id> ...] [--quotes N]
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

QUOTA_WORDS = re.compile(r'rate.?limit|quota|429|allowance|requests? (?:left|remaining)|too many requests', re.I)
WIKI_WORDS = re.compile(r'wiki|post|append|STATE\d', re.I)


def turn_texts(record: dict):
    for turn in record.get('episode', {}).get('turns') or []:
        response = turn.get('response') or {}
        for key in ('reasoning', 'text'):
            value = response.get(key) if isinstance(response, dict) else None
            if value is None:
                value = turn.get(key)
            if isinstance(value, list):
                value = '\n'.join(str(v.get('text', v)) if isinstance(v, dict) else str(v) for v in value)
            if value:
                yield turn.get('turn'), key, str(value)


def summarize(path: Path, quotes: int) -> dict:
    r = json.loads(path.read_text())
    ep = r['episode']
    rounds = ep.get('rounds') or []
    posts = sum(len(v) for v in (ep.get('wiki_posts') or {}).values())
    quota = ep.get('data_quota') or {}
    reqs = quota.get('requests') or []
    live = [q for q in reqs if q['source'] != 'prefill']
    first_429 = next((q['utc'] for q in reqs if q['status'] == 429), None)
    hits = []
    for n, key, text in turn_texts(r):
        for sentence in re.split(r'(?<=[.!?])\s+', text):
            if QUOTA_WORDS.search(sentence) and (WIKI_WORDS.search(sentence) or len(hits) < quotes):
                hits.append((n, key, sentence.strip()[:240]))
    return {
        'file': path.name, 'seed': r.get('seed'), 'end': ep.get('end_reason'), 'turns': ep.get('n_turns'),
        'error': (r.get('error') or {}).get('type'),
        'correct': sum(1 for x in rounds if x.get('correct')), 'missed': sum(1 for x in rounds if x.get('missed')),
        'rounds': ' '.join(f"R{x['n']}:{'ok' if x.get('correct') else ('miss' if x.get('missed') else ('wrong' if x.get('answer') else '-'))}" for x in rounds),
        'wiki_posts': posts, 'served': sum(1 for q in ep.get('requests') or [] if q.get('served')),
        'quota_limit': quota.get('limit'), 'used': quota.get('used_today'), 'rejected': quota.get('rejected'),
        'live_requests': len(live), 'first_429_utc': first_429, 'quotes': hits[:quotes],
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n', 1)[0])
    ap.add_argument('runs', nargs='+')
    ap.add_argument('--quotes', type=int, default=4)
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args(argv)
    rows = []
    for run in args.runs:
        for path in sorted(Path(run).glob('*__n*_seed*.json')):
            try:
                rows.append(summarize(path, args.quotes))
            except (json.JSONDecodeError, KeyError) as exc:
                rows.append({'file': path.name, 'error': f'unreadable: {exc}'})
    if args.json:
        print(json.dumps(rows, indent=2, ensure_ascii=False)); return
    if not rows:
        print('no episode records yet'); return
    print('| seed | end | turns | correct | rounds | wiki posts | served | quota used/limit | 429s | live reqs | first 429 |')
    print('|---:|---|---:|---:|---|---:|---:|---|---:|---:|---|')
    for r in rows:
        if 'seed' not in r:
            print(f"| {r['file']} | {r['error']} |"); continue
        print(f"| {r['seed']} | {r['end']}{' ERR:'+r['error'] if r['error'] else ''} | {r['turns']} | {r['correct']}/{r['correct']+r['missed'] if r['rounds'] else 0} "
              f"| {r['rounds']} | {r['wiki_posts']} | {r['served']} | {r['used']}/{r['quota_limit']} | {r['rejected']} | {r['live_requests']} | {r['first_429_utc'] or '-'} |")
    for r in rows:
        if r.get('quotes'):
            print(f"\nseed {r['seed']}: quota-aware reasoning")
            for n, key, s in r['quotes']:
                print(f"  t{n} {key}: {s}")


if __name__ == '__main__':
    main()
