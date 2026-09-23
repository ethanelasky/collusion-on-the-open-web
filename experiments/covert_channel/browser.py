"""Each participant owns a fresh Chromium process and a private browser context."""
from __future__ import annotations

import base64
import hashlib
import threading
from urllib.parse import urlsplit


_runtime = threading.local()


class Browser:
    def __init__(self, origin, token, directory):
        from playwright.sync_api import sync_playwright

        self.origin, self.directory = origin, directory
        directory.mkdir(parents=True, exist_ok=True)
        # Playwright permits one sync runtime per thread, but it can own multiple browsers.
        if not getattr(_runtime, "references", 0):
            _runtime.playwright = sync_playwright().start()
            _runtime.references = 0
        self.playwright = _runtime.playwright
        _runtime.references += 1
        self.closed = False
        try:
            self.browser = self.playwright.chromium.launch()
            self.context = self.browser.new_context(
                viewport={"width": 1100, "height": 760}, service_workers="block",
                extra_http_headers={"X-Participant": token} if token else {}, accept_downloads=False,
            )
            # Only the experiment service is available; no shared files, other sites, or sockets.
            self.context.route("**/*", self.route_request)
            self.context.route_web_socket("**/*", lambda ws: ws.close())
            self.page = self.context.new_page()
            self.page.set_default_timeout(5000)
            self.page.goto(origin)
        except BaseException:
            self.close()
            raise
        self.index = 0

    def allowed(self, url):
        u, base = urlsplit(url), urlsplit(self.origin)
        return (u.scheme, u.netloc) == (base.scheme, base.netloc)

    def route_request(self, route):
        route.continue_() if self.allowed(route.request.url) else route.abort()

    def observe(self):
        path = self.directory / f"{self.index:04d}.png"
        self.index += 1
        data = self.page.screenshot(path=str(path))
        return {"text": f"Browser URL: {self.page.url}\n" + self.page.locator("body").inner_text()[:12000],
                "image": base64.b64encode(data).decode(), "screenshot": str(path),
                "sha256": hashlib.sha256(data).hexdigest()}

    def act(self, action):
        op = action["action"]
        if op == "navigate":
            url = action["url"]
            if not isinstance(url, str) or not self.allowed(url):
                raise ValueError("Only the experiment service is available")
            self.page.goto(url, wait_until="load")
        elif op == "click":
            x, y = action["x"], action["y"]
            if not all(isinstance(v, (int, float)) and 0 <= v < limit for v, limit in ((x, 1100), (y, 760))):
                raise ValueError("Click coordinates are outside the viewport")
            self.page.mouse.click(x, y)
            if self.page.locator("#result").count():
                from playwright.sync_api import expect
                expect(self.page.locator("#result")).not_to_have_text("Loading")
        elif op == "type":
            value = action["text"]
            if not isinstance(value, str) or len(value) > 2048:
                raise ValueError("Text must have at most 2048 characters")
            self.page.keyboard.insert_text(value)
        elif op == "press":
            key = action["key"]
            if key not in ("Tab", "Enter", "Backspace", "Delete", "ArrowLeft", "ArrowRight", "ControlOrMeta+A"):
                raise ValueError("Unsupported key")
            self.page.keyboard.press(key)
        elif op != "read":
            raise ValueError("Unknown browser action")
        return self.observe()

    def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            if hasattr(self, "browser"):
                self.browser.close()
        finally:
            _runtime.references -= 1
            if not _runtime.references:
                self.playwright.stop()


def model_messages(messages, transport):
    """Keep saved text + PNG inputs provider-neutral; adapt only at the API boundary."""
    result = []
    for message in messages:
        content = message["content"]
        image = message.get("image")
        if image:
            if transport == "anthropic":
                content = [{"type": "text", "text": content},
                           {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": image}}]
            elif transport == "responses":
                content = [{"type": "input_text", "text": content},
                           {"type": "input_image", "image_url": "data:image/png;base64," + image}]
            elif transport == "openai":
                content = [{"type": "text", "text": content},
                           {"type": "image_url", "image_url": {"url": "data:image/png;base64," + image}}]
        result.append({"role": message["role"], "content": content})
    return result
