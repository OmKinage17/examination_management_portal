"""
Notification Service using real SMTP email delivery.
Supports plain-text and HTML emails.
Configured via Flask application environment settings.
"""
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from flask import current_app


def send_email(to_email: str, subject: str, body_text: str, body_html: str = None) -> bool:
    """
    Sends an email message via SMTP.
    Raises an exception or returns True on successful dispatch.
    """
    host = current_app.config.get("SMTP_HOST", "localhost")
    port = current_app.config.get("SMTP_PORT", 1025)
    user = current_app.config.get("SMTP_USER", "")
    password = current_app.config.get("SMTP_PASSWORD", "")
    sender = current_app.config.get("SMTP_SENDER", "noreply@emp.local")
    use_tls = current_app.config.get("SMTP_USE_TLS", False)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to_email

    # Plain text version
    msg.attach(MIMEText(body_text, "plain"))

    # HTML version (if provided)
    if body_html:
        msg.attach(MIMEText(body_html, "html"))

    # Dispatch via SMTP
    with smtplib.SMTP(host, port, timeout=10) as server:
        if use_tls:
            server.starttls()
        if user and password:
            server.login(user, password)
        server.send_message(msg)

    return True

