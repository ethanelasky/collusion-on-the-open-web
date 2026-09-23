"""Build a reviewable counterfactual from captured Brave responses; no network calls."""
from pathlib import Path
from copy import deepcopy
import hashlib
import json
import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
original = json.loads((HERE / 'response.json').read_text())
host = json.loads((HERE / 'wiki-host-response.json').read_text())['web']['results'][0]
spec = yaml.safe_load((ROOT / 'wikitasks/sector61_state_sparse.yaml').read_text())

def find_variant(node):
    if isinstance(node, dict):
        if 'sparse_costly_v5' in node:
            return node['sparse_costly_v5']
        for child in node.values():
            found = find_variant(child)
            if found is not None:
                return found
    elif isinstance(node, list):
        for child in node:
            found = find_variant(child)
            if found is not None:
                return found

variant = find_variant(spec)
intro = next(p['text'] for p in variant['wiki_inject'] if p['page'] == 'WorkforceLookupNotes')
paragraphs = intro.split('\n\n')
url = 'https://wikiservice.at/dse/wiki.cgi?action=browse&id=WorkforceLookupNotes'
entry = {k: deepcopy(v) for k, v in host.items() if k in {
    'title', 'url', 'is_source_local', 'is_source_both', 'description', 'profile',
    'language', 'family_friendly', 'type', 'subtype', 'is_live', 'meta_url'}}
entry.update(title=paragraphs[0].strip('= \n'), url=url,
             description=paragraphs[1].strip(), language='en')
entry['profile']['url'] = url
candidate = deepcopy(original)
candidate['web']['results'][4] = entry
patch = [{'op': 'replace', 'path': '/web/results/4', 'value': entry}]
assert len(candidate['web']['results']) == len(original['web']['results']) == 10
restored = deepcopy(candidate)
restored['web']['results'][4] = original['web']['results'][4]
assert restored == original
for name, value in [('response.with-wiki.json', candidate), ('wiki-edit.patch.json', patch)]:
    (HERE / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
provenance = {
    'status': 'reviewable candidate; not selected by an experiment arm',
    'original_response_sha256': hashlib.sha256((HERE/'response.json').read_bytes()).hexdigest(),
    'host_metadata_response_sha256': hashlib.sha256((HERE/'wiki-host-response.json').read_bytes()).hexdigest(),
    'edited_response_sha256': hashlib.sha256((HERE/'response.with-wiki.json').read_bytes()).hexdigest(),
    'edit': 'Replace only result 5; preserve all 9 other results and all top-level metadata exactly as JSON values.',
    'wiki_title_and_description_source': 'Verbatim heading and opening paragraph of configured sparse_costly_v5 WorkforceLookupNotes.',
    'wiki_host_metadata_source': 'Live Brave result for the real DseWiki homepage; its profile name, favicon, and path are retained.',
    'constructed_fields': ['title', 'url', 'description', 'language', 'profile.url'],
    'omitted_host_fields': 'Homepage cluster/sitelinks are not attached to the injected notes page.',
    'historical_status': 'Retrieved 2026-09-12 UTC; not a verified pre-June search snapshot.',
    'runtime': 'No changes to tools, routing, clocks, active arms, or historical runs.',
    'validation': 'Undoing the single replacement recovers the original JSON exactly; no credential included.'
}
(HERE/'candidate-provenance.json').write_text(json.dumps(provenance,indent=2)+'\n')
print(json.dumps({'results':10,'unchanged_results':9,'replaced_rank':5,'verified':True}))
