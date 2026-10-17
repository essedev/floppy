"""
The page the app's magic link opens, served by the API itself so the app's sign-in needs no
admin web. Opening it changes nothing (mail scanners prefetch links): the button posts the
token, and only then the request completes and the browser goes to the app's loopback.
The text is Italian, the language of the people who receive the mail.
"""

from html import escape

_STYLE = (
    "body{font:16px/1.5 -apple-system,system-ui,sans-serif;background:#f4f2ee;color:#1c1b19;"
    "display:grid;place-items:center;min-height:100vh;margin:0;padding:16px}"
    "main{max-width:420px;background:#fff;border-radius:14px;padding:28px;box-shadow:0 1px 3px #0002}"
    "h1{font-size:20px;margin:0 0 8px}p{margin:0 0 20px;color:#4a4740}"
    "button{font:inherit;background:#1c1b19;color:#fff;border:0;border-radius:10px;padding:10px 18px;cursor:pointer}"
    "@media (prefers-color-scheme:dark){body{background:#151413;color:#eee}main{background:#22211f}"
    "p{color:#b5b0a6}button{background:#eee;color:#151413}}"
)

CONTENT_SECURITY_POLICY = "default-src 'none'; style-src 'unsafe-inline'; form-action 'self' http://127.0.0.1:*; frame-ancestors 'none'"


def _page(title: str, body: str) -> str:
    return (
        '<!doctype html><html lang="it"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{escape(title)}</title><style>{_STYLE}</style></head>"
        f"<body><main>{body}</main></body></html>"
    )


def confirm(token: str, action: str) -> str:
    """The page with the button that completes the app's sign-in."""
    return _page(
        "Floppy · Collega il Mac",
        "<h1>Collega questo Mac a Floppy</h1>"
        "<p>Premi il pulsante: l'app Floppy si apre e finisce l'accesso da sola.</p>"
        f'<form method="post" action="{escape(action)}">'
        f'<input type="hidden" name="token" value="{escape(token)}">'
        "<button type=submit>Continua</button></form>",
    )


def invalid() -> str:
    """The page for a link that expired or was already used."""
    return _page(
        "Floppy · Link scaduto",
        "<h1>Questo link non vale più</h1>"
        "<p>È scaduto o è già stato usato. Torna all'app Floppy e chiedi un nuovo link.</p>",
    )
