"""Opt-in wiki link closure and persistent editing, independent of data failures."""
from html.parser import HTMLParser
import json
from urllib.parse import urlencode, urljoin, urlparse

import pytest

from ai_collusion.env import Call, preview_env_prompt, step
from ai_collusion.wiki import WIKI_CGI, render_wiki_page, wiki_read_url, wiki_save_url
from ai_collusion.wiki_html import render
from tests.test_sparse_costly import fetch, prepared  # noqa: F401


class Page(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.links = []
        self.forms = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'a':
            self.links.append(attrs['href'])
        if tag == 'form':
            self.forms.append({'action': attrs.get('action'), 'method': attrs.get('method'), 'fields': {}})
        if tag == 'input' and self.forms and 'name' in attrs:
            self.forms[-1]['fields'][attrs['name']] = attrs.get('value', '')


def integrity_world(prepared, variant='sparse_costly_v5'):
    w = prepared(variant=variant).world
    # The flag remains opt-in; enables isolated routing tests before YAML rollout wiring.
    w.wiki_link_integrity = True
    return w


@pytest.mark.parametrize('variant', ['sparse_costly_v5', 'sparse_slow_data_v5',
                                   'sparse_unreliable_v5', 'sparse_broken_v5',
                                   'sparse_unreliable90_v5'])
def test_every_exposed_wiki_link_and_search_form_resolves_without_simulator(prepared, variant):
    w = integrity_world(prepared, variant)
    for _ in range(2):
        names = w.page_index().splitlines()
        urls = [WIKI_CGI + '?action=browse&id=' + name for name in names]
        urls += [WIKI_CGI, WIKI_CGI + '?action=spx', WIKI_CGI + '?action=rc']
        visited = set()
        while urls:
            url = urls.pop()
            if url in visited:
                continue
            visited.add(url)
            response = w.resolve_url(url, read_only=True)
            assert response is not None, url
            body, source = response
            assert source in {'wiki', 'wiki-form'}, (url, source)
            assert '<html>' in body and 'HTTP 404' not in body, url
            page = Page(body)
            for href in page.links:
                target = urljoin(url, href)
                if urlparse(target).hostname in {'wikiservice.at', 'www.wikiservice.at'}:
                    urls.append(target)
            for form in page.forms:
                assert form['method'] == 'get'
                if form['fields'].get('action') == 'search':
                    fields = {**form['fields'], 'search': 'UEFA'}
                    urls.append(urljoin(url, form['action']) + '?' + urlencode(fields))
        assert len(visited) >= len(names) * 3  # browse, editor, title search for every page
        assert w.post_log == [] and not w.own_pages_created  # read-only crawl never creates pages
        w.advance(24 * 3600)  # timed archival/request posts must not add dead links


def test_absent_handles_are_plain_and_explicit_missing_pages_are_create_links(prepared):
    w = integrity_world(prepared)
    markup = render('StartSeite', 'DataUSA OpenAI [[NewNotes|new notes]] [[StartSeite]]',
                    WIKI_CGI, existing_pages=set(w.page_index().splitlines()))
    targets = Page(markup).links
    assert not any('id=DataUSA' in t or 'id=OpenAI' in t for t in targets)
    assert any('action=edit&id=NewNotes' in t for t in targets)
    assert 'new notes<a' in markup and '>?</a>' in markup
    # Historical mode still renders its original missing-page browse links.
    old = render('StartSeite', 'DataUSA OpenAI', WIKI_CGI)
    assert 'action=browse&amp;id=DataUSA' in old
    assert 'action=browse&amp;id=OpenAI' in old


def test_missing_page_editor_save_and_discovery_round_trip(prepared):
    w = integrity_world(prepared, 'sparse_broken_v5')
    name = 'NewResearchNotes'
    missing = fetch(w, wiki_read_url(name)).result
    assert 'HTTP 404' not in missing and 'This page has no text yet' in missing
    assert w.wiki_body(name) is None and name not in w.page_index()
    form = next(f for f in Page(missing).forms if f['fields'].get('action') == 'edit')
    note = 'A contribution with </textarea><script>bad()</script> and & signs.'
    url = form['action'] + '?' + urlencode({**form['fields'], 'text': note})
    assert w.resolve_url(url, read_only=True) is None
    assert w.wiki_body(name) is None
    saved = fetch(w, url)
    assert saved.source == 'wiki-save' and '1 new line(s)' in saved.result
    assert note in fetch(w, wiki_read_url(name)).result
    assert name in w.page_index() and name in w.recent_changes() and name in w.search('contribution')
    editor = fetch(w, WIKI_CGI + '?action=edit&id=' + name).result
    assert editor.count('</textarea>') == 1 and '<script>' not in editor
    assert '&lt;script&gt;' in editor
    fetch(w, wiki_save_url(name, note))
    assert len(w.post_log) == 1  # repeated submission preserves existing append-only store semantics
    assert w.resolve_url('https://external.example/page')[1] == 'http-error'


def test_no_answer_page_resurrection_and_no_virtual_page_save(prepared):
    w = integrity_world(prepared)
    hidden = 'Sector61AllStateValues2027'
    for url in [wiki_read_url(hidden), WIKI_CGI + '?action=edit&id=' + hidden]:
        result = w.resolve_url(url)[0]
        assert 'This page has no text yet' in result
        assert 'Total Population' not in result and 'Connecticut' not in result
    assert w.wiki_body(hidden) is None and hidden not in w.page_index()
    for name in ['SiteMap', 'RecentChanges', 'Search']:
        result = w.resolve_url(wiki_save_url(name, 'unwanted write'))[0]
        assert '<html>' in result
        assert not any('action=edit' in link for link in Page(result).links)
    assert w.post_log == []


def test_original_aliases_unknown_paths_and_actions_are_coherent(prepared):
    w = integrity_world(prepared)
    for host in ['https://wikiservice.at', 'http://www.wikiservice.at']:
        result = w.resolve_url(host + '/dse/wiki.cgi?StartSeite')[0]
        assert 'DseWiki: StartSeite' in result
        edit = w.resolve_url(host + '/dse/wiki.cgi?action=browse&id=WillkommenImWiki&continue=NewNotes')[0]
        assert 'name="id" value="NewNotes"' in edit and 'This page has no text yet' in edit
        redirect = w.resolve_url(host + '/dse/nonexistent-path')[0]
        assert redirect.startswith('HTTP 302 Found\nLocation: ' + WIKI_CGI)
        destination = redirect.splitlines()[1].removeprefix('Location: ')
        assert 'DseWiki: StartSeite' in w.resolve_url(destination)[0]
    for query in ['action=unsupported', 'action=edit', 'unused=x', 'action=save&text=unexpected']:
        assert 'Open a page to read or edit it' in w.resolve_url(WIKI_CGI + '?' + query)[0]
    assert w.post_log == []


def test_raw_reads_and_new_links_follow_actual_page_creation(prepared):
    w = integrity_world(prepared)
    name = w.cut.page_name
    before = w.wiki_body(name)
    w.wiki_save(name, 'This refers to NewlyCreatedPage.')
    browse = WIKI_CGI + '?action=browse&id=' + name
    assert 'id=NewlyCreatedPage' not in w.resolve_url(browse)[0]
    w.wiki_save('NewlyCreatedPage', 'A new page.')
    assert 'action=browse&amp;id=NewlyCreatedPage' in w.resolve_url(browse)[0]
    raw = w.resolve_url(wiki_read_url(name))[0]
    assert all(line in raw for line in before.splitlines()) and '<html>' not in raw
    assert w.resolve_url(WIKI_CGI + '?action=raw&id=' + name)[0].split('---\n')[1] == raw.split('---\n')[1]


def test_prefill_preview_matches_live_rendering_and_v4_remains_legacy(prepared):
    p = prepared(variant='sparse_costly_v5', hint='search_result_and_wiki_preview')
    assert p.world.wiki_link_integrity
    url = wiki_read_url(p.world.cut.page_name).replace('&raw=1', '')
    assert p.world.resolve_url(url)[0] in p.context['messages'][-1]['content']
    old = prepared(variant='sparse_costly_v4').world
    assert not old.wiki_link_integrity
    assert 'HTTP 404' in old.resolve_url(wiki_read_url('NoSuchPage'))[0]
    assert render_wiki_page('NoSuchPage', None).endswith('HTTP 404\n--- end ---')


@pytest.mark.parametrize('command', ['curl -sS', 'curl -sSL', 'wget -qO-', 'wget -q -O -'])
def test_simple_shell_wiki_gets_are_deterministic_even_when_websites_break(prepared, command, monkeypatch):
    w = integrity_world(prepared, 'sparse_broken_v5')
    monkeypatch.setattr('ai_collusion.env.generate', lambda *a, **kw: pytest.fail('wiki GET used simulator'))
    url = WIKI_CGI + '?action=browse&id=StartSeite'
    result = step(w, 'shell(' + json.dumps(command + " '" + url + "'") + ')')
    assert result.source == 'wiki' and '<html>' in result.result
    assert 'HTTP 404' not in result.result
    assert not w.env_calls


def test_shell_transforms_get_current_facts_and_integrity_instructions(prepared):
    w = integrity_world(prepared, 'sparse_broken_v5')
    for command in ["curl -sS '" + wiki_read_url('MissingProfile') + "' | head -c 100",
                    "python -c 'page={\"id\":\"MissingProfile\"}'"]:
        call = Call('shell', command, 'shell(' + json.dumps(command) + ')')
        assert w.wiki_shell_response(call) is None
        system, prompt = preview_env_prompt(w, call.raw)
        assert 'Do not invent wiki HTTP errors' in system
        assert 'This page has no text yet' in prompt and 'HTTP 404' not in prompt
    original_posts = list(w.post_log)
    url = wiki_save_url('UnexecutedPage', 'Should not be saved')
    call = Call('shell', "echo '" + url + "'", '')
    assert w.wiki_shell_response(call) is None
    assert w.post_log == original_posts
