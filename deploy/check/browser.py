"""
The admin web in Google Chrome (headless, through Playwright) behind the check's Traefik:
every CSP violation the page reports, and whether the app rendered. Run by check.py as
`uv run --with playwright python browser.py <url> <screenshot>`; prints one JSON line.
"""

import json
import sys
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
# Collected before any script of the page runs.
LISTEN = """
window.__violations = [];
document.addEventListener('securitypolicyviolation', (e) =>
  window.__violations.push(`${e.violatedDirective} blocked ${e.blockedURI || 'inline'}`));
"""


def main() -> None:
    url, shot = sys.argv[1], sys.argv[2]
    host = urlsplit(url).hostname
    with sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path=CHROME, args=[f"--host-resolver-rules=MAP {host} 127.0.0.1"]
        )
        page = browser.new_context(ignore_https_errors=True).new_page()
        console: list[str] = []
        page.on(
            "console",
            lambda m: console.append(m.text)
            if m.type == "error" and "Content Security Policy" in m.text
            else None,
        )
        page.on("pageerror", lambda e: console.append(f"page error: {e}"))
        page.add_init_script(LISTEN)
        page.goto(url, wait_until="networkidle")
        page.wait_for_timeout(1000)
        violations = page.evaluate("window.__violations") + console
        rendered = page.evaluate("document.querySelectorAll('#root *').length")
        page.screenshot(path=shot, full_page=True)
        browser.close()
    print(json.dumps({"violations": violations, "rendered": rendered}))


if __name__ == "__main__":
    main()
