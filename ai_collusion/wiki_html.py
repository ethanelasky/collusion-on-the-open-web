"""DseWiki HTML appearance adapted from the original ProWiki page.

Source: June 17, 2026 Internet Archive capture of DseWiki StartSeite.
Capture details and explicit adaptations: fixtures/wiki/dse-layout.provenance.json.
Only presentation lives here; page text, edits, and clocks belong to World.
"""
from html import escape
import re
from urllib.parse import quote


def edit_form(name: str, body: str, cgi: str) -> str:
    """The existing GET editor, escaped independently of page markup."""
    return (f'<form action="{escape(cgi, quote=True)}" method="get">'
            '<input type="hidden" name="action" value="edit">'
            f'<input type="hidden" name="id" value="{escape(name, quote=True)}">'
            f'<textarea name="text" rows="24" cols="80">{escape(body)}</textarea>'
            '<br><input type="submit" name="Save" value="Save"></form>')


def render(name: str, body: str, cgi: str, *, existing_pages: set[str] | None = None,
           content_html: str | None = None, dse_format: bool = False, last_change: str | None = None) -> str:
    """Render archival-style chrome; an optional page inventory prevents dead autolinks.

    content_html is internal, already escaped UI (the edit form), never wiki source.
    Omitting the inventory preserves the historical v4 rendering byte for byte.
    """
    if dse_format:
        from .wiki_html_v7 import render as original_format
        return original_format(name, body, cgi, existing_pages=existing_pages,
                               content_html=content_html, last_change=last_change)

    def browse(page):
        return cgi + '?action=browse&id=' + quote(page, safe='')

    def link(target, label, kind='body', title=None):
        title_attr = f" title='{escape(title, quote=True)}'" if title else ''
        return f"<a href='{escape(target, quote=True)}' class='{kind}'{title_attr}>{escape(label)}</a>"

    def inline(text):
        tokens = re.split(r'(\[\[[^\]\n]+\]\]|\[https?://[^\]\n]+\]|https?://[^\s<>]+|\b[A-Z][a-z]+(?:[A-Z][A-Za-z0-9]*)+\b)', text)
        out = []
        for token in tokens:
            if token.startswith('[[') and token.endswith(']]'):
                page, _, label = token[2:-2].partition('|')
                if existing_pages is not None and page not in existing_pages:
                    # ProWiki marks explicit missing references with an editable '?'.
                    out.append(escape(label or page) + link(
                        cgi + '?action=edit&id=' + quote(page, safe=''), '?', title='create page ' + page))
                else:
                    out.append(link(browse(page), label or page))
            elif token.startswith(('[http://', '[https://')) and token.endswith(']'):
                target, _, label = token[1:-1].partition(' ')
                out.append(link(target, label or target))
            elif token.startswith(('http://', 'https://')):
                out.append(link(token, token))
            elif re.fullmatch(r'[A-Z][a-z]+(?:[A-Z][A-Za-z0-9]*)+', token):
                out.append(link(browse(token), token) if existing_pages is None or token in existing_pages
                           else escape(token))
            else:
                out.append(escape(token))
        return ''.join(out)

    # This instruction is still present in raw page text; the original-style
    # navigation/footer supplies the edit link in the HTML representation.
    body = re.sub(r'\n+----\nEdit text of this page: https?://[^\n]+\s*$', '', body)
    paragraphs = []
    for paragraph in re.split(r'\n\s*\n', body.rstrip()):
        heading = re.fullmatch(r'=+\s*(.*?)\s*=+', paragraph)
        lines = paragraph.splitlines()
        if heading:
            paragraphs.append('<h2>' + escape(heading[1]) + '</h2>')
        elif lines and all(line.startswith('* ') for line in lines):
            paragraphs.append('<ul>\n' + '\n'.join('<li>' + inline(line[2:]) + '</li>' for line in lines) + '\n</ul>')
        else:
            paragraphs.append('<p>' + '<br />\n'.join(inline(line) for line in lines) + '</p>')
    edit = cgi + '?action=edit&id=' + quote(name, safe='')
    editable = existing_pages is None or name not in {'RecentChanges', 'SiteMap', 'Search'}
    navigation = [
        link(cgi + '?StartSeite', 'StartSeite', 'nav', 'front page'),
        link(cgi + '?action=browse&id=RecentChanges&lang=1', 'Neues', 'nav', 'recent changes'),
        link(cgi + '?action=spx&lang=1', 'Index', 'nav', 'Index'),
    ]
    if editable:
        navigation.append(link(edit, 'Edit', 'nav edit', 'edit page ' + name))
    nav = ' | '.join(navigation) + '<br />'
    title = re.sub(r'(?<=[a-z0-9])(?=[A-Z])', ' ', name)
    backlink = link(cgi + '?search=' + quote(name, safe='') + '&title=off&word=on&case=on&bl=on', title, 'title')
    return f'''<!DOCTYPE HTML PUBLIC "-//IETF//DTD HTML//EN">
<html>
<head>
<title>DseWiki: {escape(name)}</title>
<meta http-equiv="Content-Type" content="text/html; charset=utf-8">
</head>
<body link="#0000cc" vlink="#000066" text="#000000" alink="#00cccc">
<table border=0 cellspacing=0 cellpadding=0 width=100% height=24>
<tr><td width=100% height=24 bgcolor="#0000cc">&nbsp;</td></tr>
</table>
<font size=3><br></font>
<font size=6><b>{backlink}</b></font><font size=3><br><b></b></font>
<font size=3>&nbsp;<br></font>
{nav}
<hr>
{content_html if content_html is not None else ''.join(paragraphs)}
<hr>
{nav}
{link(edit, 'Edit text of this page', 'nav', 'edit page ' + name) if editable else ''}
<form method='get' action='{escape(cgi, quote=True)}' id='formsearch'>
<input type='hidden' name='action' value='search' />
<input type='hidden' name='lang' value='1' />
Search: <input type='text' name='search' value='' size='20' maxlength='60' /> gesucht wird
<div style='display:inline; white-space: nowrap;'><input type='checkbox' name='title' value='on' checked />im Titel</div>
<div style='display:inline; white-space: nowrap;'><input type='checkbox' name='text' value='on' checked />im Text</div>
</form>
</body>
</html>'''
