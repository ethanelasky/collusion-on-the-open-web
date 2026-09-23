import base64
import hashlib
import re
from html import unescape
from urllib.parse import urljoin,urlparse
from pathlib import Path
from ai_collusion.wiki import wiki_read_url
from ai_collusion.wiki_html_v7 import render
from tests.test_sparse_costly import prepared,ROOT


def test_all_pages_use_source_derived_format_and_working_assets(prepared):
    p=prepared(variant='sparse_costly_v7');w=p.world
    for name in w.page_index().splitlines():
        html,_=w.resolve_url(wiki_read_url(name).replace('&raw=1',''))
        assert 'DseWikiStripBlau.gif' in html
        assert "class='title'" in html and "id='formsearch'" in html
        assert 'date of last change:' in html
        assert 'HTTP 404' not in html
        for link in re.findall(r'(?:href|src)=[\'"]([^\'"]+)',html):
            url=urljoin(wiki_read_url(name).replace('&raw=1',''),unescape(link))
            if not (urlparse(url).hostname or '').endswith('wikiservice.at'):continue
            resolved=w.resolve_url(url,read_only=True)
            assert resolved is not None,(name,url)
            assert not re.search(r'^HTTP [45]\d\d',resolved[0],re.M),(name,url)
    for name in ['DseWikiStripBlau.gif','image/icon_world.gif']:
        body,_=w.resolve_url('https://www.wikiservice.at/dse/'+name)
        decoded=base64.b64decode(body.split('\n\n',1)[1])
        assert decoded==(ROOT/'ai_collusion/fixtures/wiki'/name.split('/')[-1]).read_bytes()


def test_source_heading_and_paragraph_style_and_editor(prepared):
    html=render('Example','== A section ==\nFirst line\nsecond line\n\n----\n\n* one\n* two','https://wikiservice.at/dse/wiki.cgi',existing_pages={'Example'})
    assert "bgcolor='#aaddff'" in html and "class='h3'" in html
    assert 'First line\nsecond line' in html and 'First line<br' not in html
    assert '<li>one</li>' in html and '== A section ==' not in html
    w=prepared(variant='sparse_costly_v7').world
    editor,_=w.resolve_url('https://wikiservice.at/dse/wiki.cgi?action=edit&id=WorkforceLookupNotes')
    assert 'DseWikiStripBlau.gif' in editor and 'method="get"' in editor
    assert 'name="Save"' in editor


def test_focal_excerpts_are_verbatim_and_legacy_stays_unchanged(prepared):
    old=prepared(variant='sparse_costly_v6');new=prepared(variant='sparse_costly_v7')
    original=old.world.dump.revision('dse/StartSeite',309)['body']
    focal=new.world.wiki_body(new.world.cut.page_name)
    for line in focal.splitlines():
        if line.startswith(('Language state query helper:','Poverty county API schema:')):assert line in original
    assert 'Other Data USA queries' in focal
    assert 'Other Data USA queries' not in old.world.wiki_body(old.world.cut.page_name)
    assert old.context['messages']==new.context['messages'] and old.context['system']==new.context['system']
    legacy,_=old.world.resolve_url(wiki_read_url(old.world.cut.page_name).replace('&raw=1',''))
    assert 'DseWikiStripBlau.gif' not in legacy
