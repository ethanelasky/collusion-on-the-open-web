"""Read-only HTML over the existing World wiki methods; no separate state store."""
from contextlib import contextmanager
from dataclasses import dataclass
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import secrets
import threading
from urllib.parse import parse_qs, urlencode, urlsplit


def render(worlds, fields, token):
    index = int(fields.get("agent", "0"))
    if not 0 <= index < len(worlds):
        raise ValueError("Unknown agent")
    world = worlds[index]
    page = fields.get("page")

    def link(label, **query):
        url = "/?" + urlencode({"token": token, "agent": index, **query})
        return f'<a href="{escape(url, quote=True)}">{escape(label)}</a>'

    with world.wiki_lock:
        if page:
            title, body = page, world.wiki_body(page)
            if body is None:
                body = "This page does not exist at this agent's current clock."
            content = "<pre>" + escape(body) + "</pre>"
        else:
            title = "Shared wiki"
            content = "<h2>Recent changes</h2><pre>" + escape(world.recent_changes()) + "</pre>"
            content += "<h2>Pages</h2><ul>" + "".join(
                f"<li>{link(name, page=name)}</li>" for name in world.page_index().splitlines()) + "</ul>"
        clock = world.container_utc.isoformat()
    agents = " · ".join(link(f"agent{i}", agent=i) for i in range(len(worlds)))
    return ("<!doctype html><html><head><meta charset=utf-8>"
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            f"<title>{escape(title)}</title><style>body{{max-width:960px;margin:2rem auto;padding:0 1rem;"
            "font:16px system-ui;line-height:1.5}}pre{white-space:pre-wrap;overflow-wrap:anywhere;"
            "padding:1rem;background:#f3f4f6}a{color:#174d9a}</style></head><body>"
            f"<nav>{link('Index')} · {agents}</nav><h1>{escape(title)}</h1>"
            f"<p>Read-only view for agent{index}. Container clock: {escape(clock)}.</p>"
            "<p>This view does not call the router, advance time, or publish edits."
            " Page text is shown verbatim.</p>" + content + "</body></html>")


@dataclass
class Observer:
    url: str


@contextmanager
def serve_worlds(worlds, *, host="127.0.0.1", port=0):
    address = ipaddress.ip_address(host)
    tailnet = ipaddress.ip_network("100.64.0.0/10")
    if address.version != 4 or address.is_unspecified or not (address.is_private or address in tailnet):
        raise ValueError("Bind an exact private IPv4 address")
    if not worlds or any(w.wiki_lock is None for w in worlds):
        raise ValueError("The observer requires prepared worlds with wiki locks")
    token = secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.headers.get("Host") != f"{host}:{self.server.server_port}":
                self.send_error(403)
                return
            parsed = urlsplit(self.path)
            fields = parse_qs(parsed.query, keep_blank_values=True)
            if (parsed.path != "/" or set(fields) - {"token", "agent", "page"}
                    or any(len(v) != 1 for v in fields.values())):
                self.send_error(400)
                return
            fields = {k: v[0] for k, v in fields.items()}
            if not secrets.compare_digest(fields.get("token", "").encode(), token.encode()):
                self.send_error(403)
                return
            try:
                body = render(worlds, fields, token).encode()
            except (ValueError, TypeError):
                self.send_error(400)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'; form-action 'none'")
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer((host, port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield Observer(f"http://{host}:{server.server_port}/?token={token}")
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
