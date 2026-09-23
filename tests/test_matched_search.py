import copy
import json
from html.parser import HTMLParser
from pathlib import Path

from experiments.matched_search import install_matched_search, GOOGLE_URL
from ai_collusion.wiki import BRAVE_SEARCH_URL
from tests.test_sparse_costly import prepared, ROOT


class Results(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows=[];self.row=None;self.field=None;self.in_cite=False
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if tag=='article':self.row={};self.rows.append(self.row)
        if self.row is None:return
        if tag=='a':self.row['url']=attrs['href']
        if tag=='h2':self.field='title'
        if tag=='p':self.field='description'
        if tag=='cite':self.in_cite=True;self.field='site_display'
        if tag=='span' and self.in_cite:self.field='site_name'
    def handle_data(self,data):
        if self.row is not None and self.field:self.row[self.field]=self.row.get(self.field,'')+data
    def handle_endtag(self,tag):
        if tag=='span' and self.in_cite:self.field='site_display'
        elif tag in ('h2','p','cite'):self.field=None
        if tag=='cite':self.in_cite=False
        if tag=='article':self.row=None


def test_same_ten_complete_result_records_in_both_formats(prepared):
    p=install_matched_search(prepared(variant='sparse_costly_v7'),'google',root=ROOT)
    parser=Results();parser.feed(p.world.search_fixtures[GOOGLE_URL])
    js=json.loads(p.world.search_fixtures[BRAVE_SEARCH_URL])['web']['results']
    canonical=p.context['provenance']['search']['canonical_results']
    assert len(parser.rows)==len(js)==len(canonical)==10
    for h,j,c in zip(parser.rows,js,canonical):
        for k in ('title','url','description'):assert h[k]==j[k]==c[k]
        assert h['site_name']==j['profile']['name']==c['site_name']
        assert h['site_display'].strip()==j['meta_url']['hostname']+' '+j['meta_url']['path']
        assert j['profile']['url']==j['url']
        assert set(j)=={'type','title','url','description','profile','meta_url'}
    assert js[4]['title']=='Workforce lookup notes'
    assert js[4]['description'] in p.world.wiki_body('WorkforceLookupNotes')


def test_only_search_call_and_body_differ_and_world_caches_match(prepared):
    pair=[install_matched_search(prepared(variant='sparse_costly_v7'),provider,root=ROOT)
          for provider in ('google','brave')]
    normalized=[]
    for p in pair:
        ctx=copy.deepcopy(p.context);search=ctx['provenance'].pop('search')
        call='web_fetch('+json.dumps(search['url'])+')'
        i=next(i for i,m in enumerate(ctx['messages']) if m['role']=='assistant' and m['content']==call)
        del ctx['messages'][i:i+2]
        normalized.append(ctx)
        assert p.world.history[-1]['call']==call
        assert search['result_body'].strip() in p.world.history[-1]['result']
    assert normalized[0]==normalized[1]
    a,b=pair
    assert a.world.search_fixtures==b.world.search_fixtures
    assert a.world.task_s==b.world.task_s
    assert a.world.container_utc==b.world.container_utc
    assert a.world.page_index()==b.world.page_index()
    for url in (GOOGLE_URL,BRAVE_SEARCH_URL,BRAVE_SEARCH_URL+'&offset=1',
                'https://www.google.com/search?q=other',
                'https://wikiservice.at/dse/wiki.cgi?action=browse&id=WorkforceLookupNotes'):
        assert a.world.resolve_url(url,read_only=True)==b.world.resolve_url(url,read_only=True)


def test_broken_v7_keeps_matched_prefix_and_wiki_available(prepared):
    a, b = [install_matched_search(prepared(variant=v), 'brave', root=ROOT)
            for v in ('sparse_costly_v7', 'sparse_broken_v7')]
    assert a.context_sha256 == b.context_sha256
    assert a.world.search_fixtures == b.world.search_fixtures
    assert a.world.page_index() == b.world.page_index()
    assert a.world.task_s == b.world.task_s
    differences = {k for k in a.resolved_config['cut'].keys() | b.resolved_config['cut'].keys()
                   if a.resolved_config['cut'].get(k) != b.resolved_config['cut'].get(k)}
    assert differences == {'website_errors', 'data_single_state_s'}
    api_url, _ = b.world._state_fetch('Connecticut')
    assert b.world.resolve_url(api_url, read_only=True)[0].startswith('HTTP 429')
    wiki_url = 'https://wikiservice.at/dse/wiki.cgi?action=browse&id=WorkforceLookupNotes'
    assert b.world.resolve_url(wiki_url, read_only=True) == a.world.resolve_url(wiki_url, read_only=True)


def test_slow_wiki_prefill_exposes_only_focal_page_as_context(prepared):
    from ai_collusion.judge import project_record
    from experiments.matched_search import refresh_focal_wiki_preview

    baseline, exposed = [install_matched_search(
        prepared(variant='sparse_slow_data_v7', hint=hint), 'brave', root=ROOT)
        for hint in ('search_result', 'search_result_and_wiki_preview')]
    refresh_focal_wiki_preview(exposed)
    a, b = baseline.context['messages'], exposed.context['messages']
    assert baseline.context['system'] == exposed.context['system']
    assert len(b) == len(a) + 2
    assert a[:-1] == b[:-3]
    # Existing preparation moves the identical status trailer to the final result.
    assert a[-1]['content'].startswith(b[-3]['content'])
    trailer = a[-1]['content'][len(b[-3]['content']):]
    assert b[-1]['content'].endswith(trailer)
    call = b[-2]['content']
    assert 'action=browse&id=WorkforceLookupNotes' in call
    assert 'WorkforceLookupDiscussion' not in call
    url = json.loads(call[len('web_fetch('):-1])
    body, source = exposed.world.resolve_url(url, read_only=True)
    assert source == 'wiki' and body in b[-1]['content']
    assert 'DseWikiStripBlau.gif' in body and 'Save=1' in body
    assert exposed.world.history[-1]['call'] == call
    assert baseline.world.task_s == exposed.world.task_s
    assert baseline.world.container_utc == exposed.world.container_utc
    assert baseline.resolved_config['cut'] == exposed.resolved_config['cut']
    projection = project_record({'context': exposed.context, 'episode': {'turns': []}})
    assert projection['context']['context_only'] and not projection['turns']
