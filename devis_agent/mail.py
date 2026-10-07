"""Envoi d'emails pour Hugo via le SMTP Brevo de Justicio (mêmes variables qu'Iris)."""

import logging
import os
import re
import smtplib
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

log = logging.getLogger(__name__)
EMAIL_RE = re.compile(r"^[^@\s<>]+@[^@\s<>]+\.[a-z]{2,}$", re.I)


def valide(addr: str) -> bool:
    return bool(addr and EMAIL_RE.match(addr.strip()))


def configure() -> bool:
    return bool(os.environ.get("BREVO_SMTP_KEY"))


def envoyer(dest: str, sujet: str, html: str, texte: str, piece: tuple[str, bytes] | None = None,
            reply_to: str | None = None, nom_expediteur: str = "Hugo · Standia") -> tuple[bool, str]:
    key = os.environ.get("BREVO_SMTP_KEY")
    if not key:
        return False, "L'envoi d'email n'est pas encore configuré sur le serveur."
    if not valide(dest):
        return False, "Adresse email invalide."
    login = os.environ.get("BREVO_SMTP_LOGIN", "support@justicio.fr")
    sender = os.environ.get("IRIS_MAIL_FROM", "support@justicio.fr")
    msg = MIMEMultipart("mixed")
    msg["Subject"] = sujet
    msg["From"] = f'"{nom_expediteur}" <{sender}>'
    msg["To"] = dest.strip()
    if reply_to and valide(reply_to):
        msg["Reply-To"] = reply_to.strip()
    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(texte, "plain", "utf-8"))
    alt.attach(MIMEText(html, "html", "utf-8"))
    msg.attach(alt)
    if piece and len(piece[1]) < 8 * 1024 * 1024:
        part = MIMEApplication(piece[1], _subtype="pdf")
        part.add_header("Content-Disposition", "attachment", filename=piece[0])
        msg.attach(part)
    try:
        with smtplib.SMTP("smtp-relay.brevo.com", 587, timeout=30) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.login(login, key)
            smtp.sendmail(sender, [dest.strip()], msg.as_bytes())
        return True, f"Envoyé à {dest.strip()}."
    except Exception as exc:
        log.warning("Envoi email Hugo échoué : %s", exc)
        return False, "L'envoi a échoué. Réessayez dans un instant."
