"""OAuth-only Gmail alert ingestion with acknowledgements after successful storage."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
import time

from .. import repo
from .. import gmail_oauth
from ..gmail_oauth import get_connection, GmailOAuthError
from ..mail_imports import ImportRecord, processed_ids, acknowledge
from ..models import SearchQuery
from .base import FetchResult, Source, register
from .companies import eligible, keyword_match
from .gmail_api import GmailAPIClient
from .mail_parser import parse_alert


class MailImportError(RuntimeError):
    pass


def mail_enabled(db=None) -> bool:
    if db is None:
        return False
    connection = get_connection(db)
    db.conn.commit()
    return bool(connection and connection["enabled"] and connection["encrypted_credentials"])


@dataclass(frozen=True)
class MailSettings:
    max_messages: int = 50
    max_seconds: int = 120
    reprocess_review: bool = False

    @classmethod
    def from_env(cls):
        try:
            settings = cls(int(os.environ.get("MAIL_MAX_MESSAGES", "50")),
                           int(os.environ.get("MAIL_MAX_SECONDS", "120")),
                           os.environ.get("MAIL_REPROCESS_REVIEW", "false").lower() == "true")
        except ValueError:
            raise MailImportError("Los límites de correo deben ser números enteros.") from None
        if not (1 <= settings.max_messages <= 200 and 1 <= settings.max_seconds <= 600):
            raise MailImportError("Límites de correo fuera de rango: mensajes 1–200, segundos 1–600.")
        return settings


@register
class EmailAlertsSource(Source):
    name = "email_alerts"
    MAX_BYTES = 2_000_000

    def __init__(self, settings=None, client_factory=None, clock=time.monotonic):
        self.settings = settings
        self._client_factory = client_factory or GmailAPIClient
        self._clock = clock
        self._db = None
        self._pending = []

    def prepare(self, db):
        self._db = db
        self._pending = []

    def fetch(self, query: SearchQuery):
        if self._db is None:
            raise MailImportError("Ejecuta el importador mediante collect.")
        connection = get_connection(self._db)
        if not connection or not connection["enabled"] or not connection["encrypted_credentials"]:
            raise MailImportError("Conecta Gmail desde Ajustes antes de importar alertas.")
        settings = self.settings or MailSettings.from_env()
        mailbox_key = hashlib.sha256(f"gmail-api|{connection['email'].lower()}|{connection['folder']}".encode()).hexdigest()
        deadline = self._clock() + settings.max_seconds
        try:
            client = self._client_factory(gmail_oauth.access_token(self._db))
            label = client.label_id(connection["folder"])
            if label is None:
                raise MailImportError("No se encontró la etiqueta de alertas. Créala en Gmail y compruébala desde Ajustes.")
            done = processed_ids(self._db, mailbox_key, settings.reprocess_review)
            identifiers, truncated = client.message_ids(label)
        except GmailOAuthError as error:
            raise MailImportError(str(error)) from None
        except MailImportError:
            raise
        except Exception:
            raise MailImportError("No se pudo consultar Gmail o el historial de importación. Revisa la conexión y la migración de correo.") from None
        pending = [identifier for identifier in identifiers if identifier not in done]
        more_pending = truncated or len(pending) > settings.max_messages
        postings, statuses, fetched, read_errors = {}, {}, 0, 0
        for identifier in pending[:settings.max_messages]:
            if self._clock() >= deadline:
                more_pending = True
                break
            try:
                if client.message_size(identifier) > self.MAX_BYTES:
                    self._pending.append(ImportRecord(mailbox_key, identifier, "oversize", "needs_review", 0))
                    statuses["needs_review"] = statuses.get("needs_review", 0) + 1
                    continue
                raw = client.raw_message(identifier)
                if len(raw) > self.MAX_BYTES:
                    raise ValueError("message_size")
                fetched += 1
                parsed = parse_alert(raw)
                kept = []
                for posting in parsed.postings:
                    if not keyword_match(posting.title, query):
                        continue
                    if posting.raw.get("country_codes") and not eligible(posting, query):
                        continue
                    kept.append(posting)
                    postings[(posting.source, posting.external_id)] = posting
                state = parsed.status if parsed.status != "parsed" or kept else "filtered"
                statuses[state] = statuses.get(state, 0) + 1
                self._pending.append(ImportRecord(mailbox_key, identifier, hashlib.sha256(raw).hexdigest(), state, len(kept)))
            except Exception:
                # Failed reads stay unacknowledged and retry on the next run.
                read_errors += 1
        partial = more_pending or read_errors or statuses.get("needs_review", 0)
        return FetchResult(postings.values(), status="partial" if partial else "success", details={
            "fetched_messages": fetched, "message_statuses": statuses, "read_errors": read_errors,
            "more_messages_pending": more_pending, "description_incomplete": True,
        })

    def after_store(self, db):
        acknowledge(db, self._pending)
        self._pending = []
