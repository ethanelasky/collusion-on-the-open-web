"""Render source-bound editorial analysis without changing raw transcripts."""
from __future__ import annotations

from html import escape
import json
from pathlib import Path


def esc(value) -> str:
    return escape(str(value), quote=True)


def load_analysis(path: Path | None, records: list[dict]) -> tuple[dict | None, dict]:
    if path is None:
        return None, {}
    data = json.loads(path.read_text())
    reviews = data.get('records', {})
    selected = {}
    for record in records:
        digest = record['_sha256']
        if digest not in reviews:
            raise ValueError(f'No matching analysis for {record["_source"]}; source checksum must match.')
        review = reviews[digest]
        turns = {str(t['turn']) for t in record['episode']['turns']}
        if set(review.get('turns', {})) != turns:
            raise ValueError(f'Analysis must cover every recorded turn exactly once: {record["_source"]}')
        if not {str(n) for n in review.get('key_turns', [])} <= turns:
            raise ValueError('Analysis contains a reference to a missing key turn.')
        for section in ('findings', 'phases', 'dimensions'):
            for item in review.get(section, []):
                if not {str(n) for n in item.get('turns', [])} <= turns:
                    raise ValueError('Analysis contains a reference to a missing turn.')
        selected[digest] = review
    return data, selected


def links(index: int, turns: list[int]) -> str:
    return ' '.join(f'<a class="turn-link" href="#episode-{index}-turn-{n}">T{n}</a>' for n in turns)


def overview_html(data: dict | None, records: list[dict], reviews: dict) -> str:
    if data is None:
        return ''
    citations = ''.join(f'<p class="source-note">{esc(s["summary"])} '
                        f'<a href="{esc(s["url"])}" target="_blank" rel="noopener noreferrer">{esc(s["label"])} ↗</a></p>'
                        for s in data.get('sources', []))
    conclusions = ''.join(f'<li>{esc(s)}</li>' for s in data.get('conclusions', []))
    experiment = ''.join(f'<li><strong>{esc(s["title"])}</strong> {esc(s["detail"])}</li>' for s in data.get('next_test', []))
    rows = []
    for i, record in enumerate(records):
        r = reviews[record['_sha256']]
        rows.append(f'<tr><td><a href="#episode-{i}">{esc(r["label"])}</a></td><td>{esc(r["verdict"])}</td><td>{esc(r["outcome"])}</td><td>{links(i, r["key_turns"])}</td></tr>')
    count = sum(len(r['turns']) for r in reviews.values())
    return f'''<section class="analysis-overview" id="incident-analysis"><div class="eyebrow">FULL TRANSCRIPT REVIEW</div>
<h2>{esc(data['title'])}</h2><p>{esc(data['summary'])}</p>
<div class="review-coverage">{len(records)} trajectories · {count} turns with analysis · {esc(data['reviewed_at'])}</div>
<div class="scroll"><table><thead><tr><th>Trajectory</th><th>Behavior assessment</th><th>Outcome</th><th>Key evidence</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div>
<h3>What these records support</h3><ul class="analysis-list">{conclusions}</ul>
<details><summary>Incident context and sources</summary><div class="turn-body">{citations}</div></details>
<details><summary>How to improve the elicitation test</summary><div class="turn-body"><ol class="analysis-list">{experiment}</ol></div></details>
<p class="muted source-note">{esc(data['method'])}</p></section>'''


def trajectory_html(review: dict | None, index: int) -> str:
    if review is None:
        return ''
    dimensions = ''.join(f'<tr><td>{esc(d["name"])}</td><td><span class="pill">{esc(d["status"])}</span></td>'
                         f'<td>{esc(d["detail"])} {links(index, d.get("turns", []))}</td></tr>' for d in review['dimensions'])
    findings = ''.join(f'<div class="finding"><strong>{esc(f["title"])}</strong><p>{esc(f["detail"])}</p>'
                       f'{links(index, f.get("turns", []))}</div>' for f in review['findings'])
    phases = ''.join(f'<li><strong>{esc(p["label"])}</strong><p>{esc(p["detail"])}</p>{links(index,p.get("turns",[]))}</li>' for p in review['phases'])
    limits = ''.join(f'<li>{esc(s)}</li>' for s in review['limits'])
    return f'''<section class="trajectory-analysis"><div class="eyebrow">BEHAVIOR ANALYSIS</div><h3>{esc(review['verdict'])}</h3><p>{esc(review['summary'])}</p>
<div class="scroll"><table><thead><tr><th>Incident behavior</th><th>Assessment</th><th>Evidence and interpretation</th></tr></thead><tbody>{dimensions}</tbody></table></div>
<div class="findings">{findings}</div><details><summary>Full trajectory: phases and decisions</summary><div class="turn-body"><ol class="analysis-list">{phases}</ol></div></details>
<details><summary>Limits and alternative explanations</summary><div class="turn-body"><ul class="analysis-list">{limits}</ul></div></details></section>'''


def turn_html(review: dict | None, turn: int) -> str:
    if review is None:
        return ''
    note = review['turns'][str(turn)]
    return f'<aside class="turn-analysis"><span class="eyebrow">ANALYSIS · {esc(note["tag"])}</span><p>{esc(note["text"])}</p></aside>'


STYLE = '''
.analysis-overview{background:#fffefa;border:1px solid #c5d9d7;border-top:4px solid #146575;border-radius:12px;padding:26px;margin:26px 0}.review-coverage{font-size:12px;color:#146575;margin:16px 0;font-weight:650}.trajectory-analysis{border:1px solid #c5d9d7;background:#f8faf6;border-radius:9px;padding:20px;margin:24px 0}.trajectory-analysis>h3{font-size:20px;margin-top:8px}.turn-link{display:inline-block;background:#e3eeed;padding:2px 7px;border-radius:4px;font-size:11px;margin:2px}.finding{padding:15px 0;border-bottom:1px solid #dae1df}.finding:last-child{border:0}.finding p,.analysis-list p{margin:6px 0;font-size:13px}.analysis-list{padding-left:22px}.analysis-list li{margin:12px 0}.turn-analysis{border-left:3px solid #146575;background:#eaf1ee;padding:12px 16px;margin:10px 0 16px}.turn-analysis p{font-size:13px;margin:8px 0 0}.source-note{font-size:12px}.analysis-badge{font-size:10px;color:#146575;border:1px solid #c5d9d7;border-radius:4px;padding:2px 6px}@media(max-width:700px){.analysis-overview,.trajectory-analysis{padding:16px}.analysis-badge{display:none}}
'''
