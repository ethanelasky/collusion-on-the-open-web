"""V7 DseWiki HTML appearance adapted from the original ProWiki page.

Source: June 17, 2026 Internet Archive capture of DseWiki StartSeite.
Capture details and explicit adaptations: fixtures/wiki/dse-layout.provenance.json.
Only presentation lives here; page text, edits, and clocks belong to World.
"""
from html import escape
from datetime import datetime, timedelta
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
           content_html: str | None = None, last_change: str | None = None) -> str:
    """Render archival-style chrome; an optional page inventory prevents dead autolinks.

    content_html is internal, already escaped UI (the edit form), never wiki source.
    Omitting the inventory preserves the historical v4 rendering byte for byte.
    """
    def browse(page):
        return cgi + '?action=browse&id=' + quote(page, safe='')

    def link(target, label, kind='body', title=None):
        title_attr = f" title='{escape(title, quote=True)}'" if title else ''
        icon = ("<img src='https://www.wikiservice.at/dse/image/icon_world.gif' border='0' style='vertical-align: -4px;'> "
                if kind == 'body' and target.startswith(('http://', 'https://'))
                and 'wikiservice.at/dse/wiki.cgi' not in target else '')
        return f"<a href='{escape(target, quote=True)}' class='{kind}'{title_attr}>{icon}{escape(label)}</a>"

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
    # DseWiki uses blue table headings, bare paragraph separators, and ordinary
    # line wrapping; a single source newline is not an HTML line break.
    paragraphs = []
    paragraph_lines = []
    in_list = False
    def flush():
        if paragraph_lines:
            paragraphs.append('<p>\n' + '\n'.join(inline(line) for line in paragraph_lines))
            paragraph_lines.clear()
    for line in body.rstrip().splitlines():
        heading = re.fullmatch(r'(=+)\s*(.*?)\s*\1', line)
        if heading:
            flush()
            if in_list: paragraphs.append('</ul>'); in_list = False
            label = escape(heading[2])
            paragraphs.append("<br />\n<div style='display:inline; position:relative; top:-50pt;'><a name='"
                + escape(heading[2], quote=True) + "'></a></div><table width='100%' cellpadding='2' border='0' bgcolor='#aaddff'>"
                + "<tr><td width='95%'><font size='3' color='#000000' class='h3'><strong>" + label
                + "</strong></font></td><td align='right' width='5%'></td></tr></table>")
        elif re.fullmatch(r'-{4,}', line.strip()):
            flush()
            if in_list: paragraphs.append('</ul>'); in_list = False
            paragraphs.append('<hr>')
        elif line.startswith('* '):
            flush()
            if not in_list: paragraphs.append('<ul>'); in_list = True
            paragraphs.append('<li>' + inline(line[2:]) + '</li>')
        else:
            if in_list: paragraphs.append('</ul>'); in_list = False
            if line.strip(): paragraph_lines.append(line)
            else: flush()
    flush()
    if in_list: paragraphs.append('</ul>')
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
    change_note = ''
    if last_change and editable:
        local = datetime.fromisoformat(last_change.replace('Z', '+00:00')) + timedelta(hours=2)
        change_note = ' (date of last change: ' + local.strftime('%B ') + str(local.day) + local.strftime(', %Y %H:%M') + ')'
    return f'''<!DOCTYPE HTML PUBLIC "-//IETF//DTD HTML//EN">
<html>
<head>
<title>DseWiki: {escape(name)}</title>
<meta http-equiv="Content-Type" content="text/html; charset=utf-8">
</head>
<body link="#0000cc" vlink="#000066" text="#000000" alink="#00cccc">
<table border=0 cellspacing=0 cellpadding=0 width=100% height=24>
<tr><td width=100% height=24 background="https://www.wikiservice.at/dse/DseWikiStripBlau.gif" bgcolor="#0000cc">&nbsp;</td></tr>
</table>
<font size=3><br></font>
<font size=6><b>{backlink}</b></font><font size=3><br><b></b></font>
<font size=3>&nbsp;<br></font>
{nav}
<hr>
{content_html if content_html is not None else ''.join(paragraphs)}
<hr>
{nav}
{link(edit, 'Edit text of this page', 'nav', 'edit page ' + name) if editable else ''}{change_note}
<form method='get' action='{escape(cgi, quote=True)}' id='formsearch'>
<input type='hidden' name='action' value='search' />
<input type='hidden' name='lang' value='1' />
Search: <input type='text' name='search' value='' size='20' maxlength='60' /> gesucht wird
<div style='display:inline; white-space: nowrap;'><input type='checkbox' name='title' value='on' checked />im Titel</div>
<div style='display:inline; white-space: nowrap;'><input type='checkbox' name='text' value='on' checked />im Text</div>
</form>
</body>
</html>'''
