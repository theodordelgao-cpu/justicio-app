"""Envoi du DOE par email, via le SMTP Brevo déjà utilisé par Justicio.

Variables d'environnement : BREVO_SMTP_KEY (obligatoire), BREVO_SMTP_LOGIN
(défaut support@justicio.fr), IRIS_MAIL_FROM (défaut support@justicio.fr).
Le PDF est joint s'il pèse moins de 8 Mo ; le lien de téléchargement est toujours inclus.
"""

import html
import logging
import os
import re
import smtplib
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

log = logging.getLogger(__name__)

MAX_ATTACH = 8 * 1024 * 1024
EMAIL_RE = re.compile(r"^[^@\s<>]+@[^@\s<>]+\.[a-z]{2,}$", re.I)


def email_valide(addr: str) -> bool:
    return bool(addr and EMAIL_RE.match(addr.strip()))


def configure() -> bool:
    return bool(os.environ.get("BREVO_SMTP_KEY"))


def envoyer_doe(dest: str, meta: dict, lien: str, pdf: bytes | None, filename: str, message: str = "") -> tuple[bool, str]:
    key = os.environ.get("BREVO_SMTP_KEY")
    if not key:
        return False, "L'envoi d'email n'est pas encore configuré sur le serveur."
    if not email_valide(dest):
        return False, "Adresse email invalide."

    login = os.environ.get("BREVO_SMTP_LOGIN", "support@justicio.fr")
    sender = os.environ.get("IRIS_MAIL_FROM", "support@justicio.fr")
    nom = meta.get("nom", "Chantier")
    entreprise = meta.get("entreprise") or "l'entreprise"
    joint = pdf is not None and len(pdf) <= MAX_ATTACH

    msg_html = html.escape(message).replace("\n", "<br>") if message else ""
    body = f"""
    <div style="font-family:Arial,sans-serif;color:#1b2430;max-width:560px">
      <p style="font-size:12px;letter-spacing:.12em;text-transform:uppercase;color:#6b7480">Dossier des Ouvrages Exécutés</p>
      <h2 style="margin:4px 0 14px">{html.escape(nom)}</h2>
      <p>{html.escape(entreprise)} vous transmet le DOE de ce chantier.</p>
      {f'<p style="padding:12px 14px;background:#f3f5f7;border-radius:8px">{msg_html}</p>' if msg_html else ''}
      <p style="margin:22px 0">
        <a href="{html.escape(lien)}" style="background:#ff7a1a;color:#1a0b00;padding:12px 20px;border-radius:8px;text-decoration:none;font-weight:bold">Télécharger le DOE</a>
      </p>
      <p style="font-size:13px;color:#6b7480">{'Le PDF est aussi joint à cet email. ' if joint else ''}Lien : {html.escape(lien)}</p>
      <p style="font-size:12px;color:#6b7480;margin-top:28px">Dossier assemblé avec Iris · Standia</p>
    </div>"""
    text = f"{entreprise} vous transmet le DOE du chantier « {nom} ».\n\n{message}\n\nTélécharger : {lien}\n\nDossier assemblé avec Iris · Standia"

    msg = MIMEMultipart("mixed")
    msg["Subject"] = f"DOE · {nom}"
    msg["From"] = f'"Iris · Standia" <{sender}>'
    msg["To"] = dest.strip()
    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(text, "plain", "utf-8"))
    alt.attach(MIMEText(body, "html", "utf-8"))
    msg.attach(alt)
    if joint:
        part = MIMEApplication(pdf, _subtype="pdf")
        part.add_header("Content-Disposition", "attachment", filename=filename)
        msg.attach(part)

    try:
        with smtplib.SMTP("smtp-relay.brevo.com", 587, timeout=30) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.login(login, key)
            smtp.sendmail(sender, [dest.strip()], msg.as_bytes())
        return True, f"DOE envoyé à {dest.strip()}."
    except Exception as exc:
        log.warning("Envoi email Iris échoué : %s", exc)
        return False, "L'envoi a échoué. Réessayez dans un instant."
