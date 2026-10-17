"""
The text of every mail the control plane sends, in Italian, the language of the admin web
and of the people who receive them. The links point at the admin web's pages, which call
the API with the token.
"""

from urllib.parse import quote

from floppy_server.infra.mail import Mail

# The admin web's routes for the links (web/src/features/app/router.tsx); the app's link is
# a page the API serves itself (auth/app_page.py).
WEB_LINK_PATH = "/accedi/link"
APP_LINK_PATH = "/api/v1/auth/app/link"
INVITATION_PATH = "/invito"


def _url(public_url: str, path: str, token: str) -> str:
    return f"{public_url.rstrip('/')}{path}?token={quote(token)}"


def sign_in(*, to: str, organization: str | None, public_url: str, token: str, code: str) -> Mail:
    """The magic link and code for the admin web."""
    subject = f"Floppy · {organization}" if organization else "Floppy"
    text = (
        "Ciao,\n\n"
        "per entrare in Floppy apri questo link (vale 15 minuti, una volta sola):\n\n"
        f"{_url(public_url, WEB_LINK_PATH, token)}\n\n"
        f"Se leggi la mail su un altro dispositivo, scrivi questo codice: {code}\n\n"
        "Se non sei stato tu a chiederlo, ignora questa mail.\n"
    )
    return Mail(to=to, subject=f"{subject}: il tuo accesso", text=text)


def app_sign_in(
    *, to: str, organization: str | None, public_url: str, token: str, code: str, device: str
) -> Mail:
    """The magic link and code for signing in a Mac from the app."""
    subject = f"Floppy · {organization}" if organization else "Floppy"
    text = (
        "Ciao,\n\n"
        f"per collegare «{device}» a Floppy apri questo link sullo stesso Mac (vale 15 minuti):\n\n"
        f"{_url(public_url, APP_LINK_PATH, token)}\n\n"
        f"Oppure scrivi questo codice nell'app: {code}\n\n"
        "Se non sei stato tu, ignora questa mail: senza il link o il codice nessun Mac viene collegato.\n"
    )
    return Mail(to=to, subject=f"{subject}: collega il tuo Mac", text=text)


def not_a_member(*, to: str) -> Mail:
    """The answer to a sign-in from an address with no team: ask your admin."""
    text = (
        "Ciao,\n\n"
        "qualcuno ha chiesto di entrare in Floppy con questo indirizzo, ma non fa parte di nessun team.\n"
        "Se ti aspettavi un invito, chiedi all'amministratore del tuo team di invitarti con questo indirizzo.\n\n"
        "Se non sei stato tu, ignora questa mail.\n"
    )
    return Mail(to=to, subject="Floppy: nessun team con questo indirizzo", text=text)


def invitation(*, to: str, organization: str, inviter: str, public_url: str, token: str) -> Mail:
    """An invitation to an organization, valid 7 days."""
    text = (
        "Ciao,\n\n"
        f"{inviter} ti ha invitato in {organization} su Floppy, il disco condiviso del team.\n\n"
        "Per entrare apri questo link (vale 7 giorni):\n\n"
        f"{_url(public_url, INVITATION_PATH, token)}\n"
    )
    return Mail(to=to, subject=f"Floppy · {organization}: sei stato invitato", text=text)


def mail_test(*, to: str, organization: str) -> Mail:
    """The settings page's test message."""
    text = (
        f"Questa è una prova dalle impostazioni di {organization}: la posta di Floppy funziona.\n"
    )
    return Mail(to=to, subject=f"Floppy · {organization}: prova di invio", text=text)
