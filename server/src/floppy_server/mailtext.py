"""
The text of every mail the control plane sends, in Italian or English: the organization's
`mail_language`, or for an address in no organization the request's `Accept-Language`
(DATABASE_SCHEMA.md, organizations). The links point at the admin web's pages, which call the
API with the token; the app's link is a page the API serves itself (auth/app_page.py).
"""

from typing import Literal
from urllib.parse import quote

from floppy_server.infra.mail import Mail

Language = Literal["it", "en"]
LANGUAGES: tuple[Language, ...] = ("it", "en")

# The admin web's routes for the links (web/src/features/app/router.tsx).
WEB_LINK_PATH = "/accedi/link"
APP_LINK_PATH = "/api/v1/auth/app/link"
INVITATION_PATH = "/invito"


def _url(public_url: str, path: str, token: str) -> str:
    return f"{public_url.rstrip('/')}{path}?token={quote(token)}"


def _subject(organization: str | None) -> str:
    return f"Floppy · {organization}" if organization else "Floppy"


def language_of(accept_language: str | None, default: Language) -> Language:
    """Italian or English from an `Accept-Language` header, in the client's order of preference."""
    weighted: list[tuple[float, int, str]] = []
    for position, part in enumerate((accept_language or "").split(",")):
        tag, _, params = part.strip().partition(";")
        quality = 1.0
        if params.strip().startswith("q="):
            try:
                quality = float(params.strip()[2:])
            except ValueError:
                quality = 0.0
        weighted.append((-quality, position, tag.strip().lower()[:2]))
    for _, _, primary in sorted(weighted):
        if primary == "it":
            return "it"
        if primary == "en":
            return "en"
    return default


def sign_in(
    *, lang: Language, to: str, organization: str | None, public_url: str, token: str, code: str
) -> Mail:
    """The magic link and code for the admin web."""
    url = _url(public_url, WEB_LINK_PATH, token)
    if lang == "en":
        text = (
            "Hello,\n\n"
            "to sign in to Floppy open this link (valid for 15 minutes, once):\n\n"
            f"{url}\n\n"
            f"If you are reading this mail on another device, type this code: {code}\n\n"
            "If you did not ask for it, ignore this mail.\n"
        )
        return Mail(to=to, subject=f"{_subject(organization)}: your sign-in", text=text)
    text = (
        "Ciao,\n\n"
        "per entrare in Floppy apri questo link (vale 15 minuti, una volta sola):\n\n"
        f"{url}\n\n"
        f"Se leggi la mail su un altro dispositivo, scrivi questo codice: {code}\n\n"
        "Se non sei stato tu a chiederlo, ignora questa mail.\n"
    )
    return Mail(to=to, subject=f"{_subject(organization)}: il tuo accesso", text=text)


def app_sign_in(
    *,
    lang: Language,
    to: str,
    organization: str | None,
    public_url: str,
    token: str,
    code: str,
    device: str,
) -> Mail:
    """The magic link and code for signing in a Mac from the app."""
    url = _url(public_url, APP_LINK_PATH, token)
    if lang == "en":
        text = (
            "Hello,\n\n"
            f"to connect “{device}” to Floppy open this link on the same Mac (valid for 15 minutes):\n\n"
            f"{url}\n\n"
            f"Or type this code in the app: {code}\n\n"
            "If it was not you, ignore this mail: without the link or the code no Mac is connected.\n"
        )
        return Mail(to=to, subject=f"{_subject(organization)}: connect your Mac", text=text)
    text = (
        "Ciao,\n\n"
        f"per collegare «{device}» a Floppy apri questo link sullo stesso Mac (vale 15 minuti):\n\n"
        f"{url}\n\n"
        f"Oppure scrivi questo codice nell'app: {code}\n\n"
        "Se non sei stato tu, ignora questa mail: senza il link o il codice nessun Mac viene collegato.\n"
    )
    return Mail(to=to, subject=f"{_subject(organization)}: collega il tuo Mac", text=text)


def not_a_member(*, lang: Language, to: str) -> Mail:
    """The answer to a sign-in from an address with no team: ask your admin."""
    if lang == "en":
        text = (
            "Hello,\n\n"
            "someone asked to sign in to Floppy with this address, but it belongs to no team.\n"
            "If you were expecting an invitation, ask your team's admin to invite this address.\n\n"
            "If it was not you, ignore this mail.\n"
        )
        return Mail(to=to, subject="Floppy: no team with this address", text=text)
    text = (
        "Ciao,\n\n"
        "qualcuno ha chiesto di entrare in Floppy con questo indirizzo, ma non fa parte di nessun team.\n"
        "Se ti aspettavi un invito, chiedi all'amministratore del tuo team di invitarti con questo indirizzo.\n\n"
        "Se non sei stato tu, ignora questa mail.\n"
    )
    return Mail(to=to, subject="Floppy: nessun team con questo indirizzo", text=text)


def invitation(
    *, lang: Language, to: str, organization: str, inviter: str, public_url: str, token: str
) -> Mail:
    """An invitation to an organization, valid 7 days."""
    url = _url(public_url, INVITATION_PATH, token)
    if lang == "en":
        text = (
            "Hello,\n\n"
            f"{inviter} invited you to {organization} on Floppy, the team's shared disk.\n\n"
            "To join, open this link (valid for 7 days):\n\n"
            f"{url}\n\n"
            "The page then shows how to install the Floppy app on your Mac.\n"
        )
        return Mail(to=to, subject=f"Floppy · {organization}: you are invited", text=text)
    text = (
        "Ciao,\n\n"
        f"{inviter} ti ha invitato in {organization} su Floppy, il disco condiviso del team.\n\n"
        "Per entrare apri questo link (vale 7 giorni):\n\n"
        f"{url}\n\n"
        "Poi la pagina ti spiega come installare l'app Floppy sul Mac.\n"
    )
    return Mail(to=to, subject=f"Floppy · {organization}: sei stato invitato", text=text)


def mail_test(*, lang: Language, to: str, organization: str) -> Mail:
    """The settings page's test message."""
    if lang == "en":
        text = f"This is a test from the settings of {organization}: Floppy's mail works.\n"
        return Mail(to=to, subject=f"Floppy · {organization}: test message", text=text)
    text = (
        f"Questa è una prova dalle impostazioni di {organization}: la posta di Floppy funziona.\n"
    )
    return Mail(to=to, subject=f"Floppy · {organization}: prova di invio", text=text)
