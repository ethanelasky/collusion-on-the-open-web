"""Matched search-result content, with HTML versus native-shaped JSON exposure.

Only the constructed search prefix changes. Both worlds receive the same cache
entries; existing clocks, episode loop, routing and tools are used unchanged.
"""
from __future__ import annotations

import copy
import hashlib
import html
import json
import re
from pathlib import Path
from urllib.parse import quote_plus

from ai_collusion.preparation import context_sha256
from ai_collusion.wiki import BRAVE_SEARCH_URL, SEARCH_QUERY, _result, _wiki_utc

GOOGLE_URL = 'https://www.google.com/search?q=' + quote_plus(SEARCH_QUERY)


def canonical_results(capture: dict) -> list[dict]:
    """Ten identical semantic records; omit unmatched optional rich-result data."""
    results = []
    for item in capture['web']['results']:
        profile = item['profile']
        results.append({
            'title': item['title'], 'url': item['url'], 'description': item['description'],
            'site_name': profile['name'], 'site_host': profile['long_name'],
            'display_path': item['meta_url']['path'],
        })
    assert len(results) == 10
    return results


def render_bodies(results: list[dict], google_template: str) -> dict[str, str]:
    assert len(results) == 10
    articles = []
    for row in results:
        e = {k: html.escape(v, quote=True) for k, v in row.items()}
        articles.append(
            '    <article>\n'
            f'      <a href="{e["url"]}">\n'
            f'        <cite><span>{e["site_name"]}</span> '
            f'{e["site_host"]} {e["display_path"]}</cite>\n'
            f'        <h2>{e["title"]}</h2>\n'
            '      </a>\n'
            f'      <p>{e["description"]}</p>\n'
            '    </article>'
        )
    google = re.sub(r'    <article>.*    </article>', lambda _: '\n'.join(articles),
                    google_template, count=1, flags=re.S)
    google = google.replace('${query}', html.escape(SEARCH_QUERY, quote=True))
    assert '${' not in google
    brave_results = []
    for row in results:
        brave_results.append({
            'type': 'search_result',
            'title': row['title'], 'url': row['url'], 'description': row['description'],
            'profile': {'name': row['site_name'], 'url': row['url'],
                        'long_name': row['site_host']},
            'meta_url': {'hostname': row['site_host'], 'path': row['display_path']},
        })
    brave = json.dumps({'type': 'search', 'query': {'original': SEARCH_QUERY},
                        'web': {'type': 'search', 'results': brave_results}},
                       ensure_ascii=False, indent=2)
    return {'google': google, 'brave': brave}


def install_matched_search(prepared, provider: str, *, root: Path):
    if provider not in ('google', 'brave'):
        raise ValueError(provider)
    capture_path = root/'ai_collusion/prompts/search/sector61_brave_v1.json'
    template_path = root/'ai_collusion/prompts/search/sector61_results_v3.html'
    capture = json.loads(capture_path.read_text())
    results = canonical_results(capture)
    # Bind the planted entry to this exact world's focal page and introduction.
    paragraphs = [p.strip() for p in re.split(r'\n\s*\n', prepared.world.wiki_body(
        prepared.world.cut.page_name)) if p.strip()]
    results[4].update(title=paragraphs[0].strip('= '), description=paragraphs[1],
        url='https://wikiservice.at/dse/wiki.cgi?action=browse&id='+prepared.world.cut.page_name)
    bodies = render_bodies(results, template_path.read_text())
    urls = {'google': GOOGLE_URL, 'brave': BRAVE_SEARCH_URL}
    context = prepared.context
    old = context['provenance']['search']
    old_call = 'web_fetch(' + json.dumps(old['url']) + ')'
    index = next(i for i,m in enumerate(context['messages'])
                 if m['role']=='assistant' and m['content']==old_call)
    assert context['messages'][index+1]['role']=='user'
    old_result = _result(old_call, old['result_body'], _wiki_utc(old['timestamp']))
    assert context['messages'][index+1]['content'].startswith(old_result)
    trailer = context['messages'][index+1]['content'][len(old_result):]
    call = 'web_fetch(' + json.dumps(urls[provider]) + ')'
    context['messages'][index]['content'] = call
    context['messages'][index+1]['content'] = _result(
        call, bodies[provider], _wiki_utc(old['timestamp'])) + trailer
    semantic_sha = hashlib.sha256(json.dumps(results, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    context['provenance']['search'] = {
        'url': urls[provider], 'query': SEARCH_QUERY, 'result_body': bodies[provider],
        'timestamp': old['timestamp'], 'constructed': True,
        'fixture_version': 'matched10-search-v1', 'provider': provider,
        'canonical_results': results, 'canonical_results_sha256': semantic_sha,
        'source_capture_path': str(capture_path),
        'source_capture_sha256': hashlib.sha256(capture_path.read_bytes()).hexdigest(),
        'google_template_sha256': hashlib.sha256(template_path.read_bytes()).hexdigest(),
        'normalization': 'Same ten result records in both formats. Optional extra snippets, '
                         'sitelinks, thumbnails, favicons, dates, article metadata and '
                         'query-enrichment metadata omitted from both exposures.',
        'approval_source': 'User requested identical results after rejecting the unmatched comparison.',
    }
    # Cache state is identical in both arms, including both URLs. Existing Brave
    # quota behavior remains enabled identically; no new routing behavior is added.
    prepared.world.search_fixtures.clear()
    prepared.world.search_fixtures.update({urls[p]: bodies[p] for p in urls})
    prepared.world.seed_history(context)
    prepared.context_sha256 = context_sha256(context)
    prepared.resolved_config['hint_provenance'] = copy.deepcopy(context['provenance'])
    prepared.resolved_config['matched_search'] = {
        'version': 'matched10-search-v1', 'provider': provider,
        'canonical_results_sha256': semantic_sha,
        'allowed_context_differences': ['prefilled search URL', 'search body representation'],
    }
    return prepared


def refresh_focal_wiki_preview(prepared):
    """Use the live focal HTML in the existing optional wiki-read prefill.

    Existing preparation supplies the call, timestamp and initial clocks. This
    only replaces its rendered body (including the live last-change footer).
    No live action or time advance is introduced.
    """
    if prepared.resolved_config['hint'] != 'search_result_and_wiki_preview':
        raise ValueError('focal preview requires the existing wiki-preview hint')
    messages = prepared.context['messages']
    call = messages[-2]['content']
    expected_url = ('https://wikiservice.at/dse/wiki.cgi?action=browse&id='
                    + prepared.world.cut.page_name)
    assert call == 'web_fetch(' + json.dumps(expected_url) + ')'
    header, previous = messages[-1]['content'].split('\n', 1)
    _, marker, trailer = previous.rpartition('--- end ---')
    assert marker and header.startswith('RESULT')
    body, source = prepared.world.resolve_url(expected_url, read_only=True)
    assert source == 'wiki' and 'DseWikiStripBlau.gif' in body
    messages[-1]['content'] = header + '\n' + body + trailer
    provenance = {'constructed': True, 'page': prepared.world.cut.page_name,
                  'url': expected_url, 'body_source': 'initial_world_read_only',
                  'body_sha256': hashlib.sha256(body.encode()).hexdigest(),
                  'intent_evidence': False, 'additional_live_time_s': 0}
    prepared.context['provenance']['wiki_preview'] = provenance
    prepared.world.seed_history(prepared.context)
    prepared.context_sha256 = context_sha256(prepared.context)
    prepared.resolved_config['hint_provenance'] = copy.deepcopy(prepared.context['provenance'])
    return prepared
