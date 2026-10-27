"""OAuth-only Gmail alert ingestion with acknowledgements after successful storage."""
from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import os
import time

from .. import repo
from .. import gmail_oauth
from ..gmail_oauth import get_connection, GmailOAuthError
from ..mail_imports import ImportRecord, processed_ids, acknowledge, PARSER_VERSION
from ..models import SearchQuery
from .base import FetchResult, Source, register
from .companies import eligible, keyword_match
from .gmail_api import GmailAPIClient
from .mail_parser import parse_alert
from .mail_urls import AlertLinkResolver
from .computrabajo_alerts import ComputrabajoAlertSearch


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
    apply_search_filters: bool = False
    unread_only: bool = True
    reprocess_all: bool = False

    @classmethod
    def from_env(cls):
        try:
            settings = cls(int(os.environ.get("MAIL_MAX_MESSAGES", "50")),
                           int(os.environ.get("MAIL_MAX_SECONDS", "120")),
                           os.environ.get("MAIL_REPROCESS_REVIEW", "false").lower() == "true",
                           os.environ.get("MAIL_APPLY_SEARCH_FILTERS", "false").lower() == "true",
                           os.environ.get("MAIL_UNREAD_ONLY", "true").lower() == "true",
                           os.environ.get("MAIL_REPROCESS_ALL", "false").lower() == "true")
        except ValueError:
            raise MailImportError("Los límites de correo deben ser números enteros.") from None
        if not (1 <= settings.max_messages <= 200 and 1 <= settings.max_seconds <= 600):
            raise MailImportError("Límites de correo fuera de rango: mensajes 1–200, segundos 1–600.")
        return settings


@register
class EmailAlertsSource(Source):
    name = "email_alerts"
    MAX_BYTES = 2_000_000

    def __init__(self, settings=None, client_factory=None, clock=time.monotonic, link_resolver_factory=None, search_factory=None):
        self.settings = settings
        self._client_factory = client_factory or GmailAPIClient
        self._clock = clock
        self._link_resolver_factory = link_resolver_factory or AlertLinkResolver
        self._search_factory = search_factory or ComputrabajoAlertSearch
        self._db = None
        self._pending = []
        self._postings = []

    def prepare(self, db):
        self._db = db
        self._pending = []
        self._postings = []

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
            done = set() if settings.reprocess_all else processed_ids(self._db, mailbox_key, settings.reprocess_review, PARSER_VERSION)
            identifiers, truncated = client.message_ids(label) if settings.unread_only else client.message_ids(label, unread_only=False)
        except GmailOAuthError as error:
            raise MailImportError(str(error)) from None
        except MailImportError:
            raise
        except Exception:
            raise MailImportError("No se pudo consultar Gmail o el historial de importación. Revisa la conexión y la migración de correo.") from None
        pending = [identifier for identifier in identifiers if identifier not in done]
        more_pending = truncated or len(pending) > settings.max_messages
        postings, statuses, fetched, read_errors = {}, {}, 0, 0
        cards_found = filtered = unresolved = 0
        providers, search_results = {}, []
        resolver = self._link_resolver_factory(deadline, clock=self._clock)
        search = self._search_factory()
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
                parsed = parse_alert(raw, resolve_url=resolver)
                extracted = list(parsed.postings)
                search_failed = False
                for url in parsed.search_links:
                    if self._clock() >= deadline:
                        search_failed = True
                        more_pending = True
                        search_results.append({"url": url, "status": "pending", "reason": "time_limit_reached"})
                        continue
                    search.max_runtime = max(0.1, min(30, deadline - self._clock()))
                    expansion = search.fetch_search(url)
                    extracted.extend(expansion)
                    search_failed = search_failed or expansion.status != "success"
                    search_results.append({"url": url, "status": expansion.status, "reason": expansion.details.get("reason", "unknown")})
                cards_found += len(extracted)
                unresolved += parsed.unresolved_links
                if parsed.provider:
                    provider = providers.setdefault(parsed.provider, {"messages": 0, "cards": 0})
                    provider["messages"] += 1
                    provider["cards"] += len(extracted)
                kept = []
                for posting in extracted:
                    if settings.apply_search_filters and (not keyword_match(posting.title, query) or (posting.raw.get("country_codes") and not eligible(posting, query))):
                        filtered += 1
                        continue
                    key = (posting.source, posting.external_id)
                    previous = postings.get(key)
                    if previous:
                        codes = sorted(set(previous.raw.get("country_codes", [])) | set(posting.raw.get("country_codes", [])))
                        posting = replace(posting, company=posting.company or previous.company,
                            location=posting.location or previous.location, country=posting.country or previous.country,
                            remote=posting.remote if posting.remote is not None else previous.remote,
                            description=max((previous.description, posting.description), key=len),
                            raw={**posting.raw, "country_codes": codes, "location_needs_review": not bool(codes)})
                    kept.append(posting)
                    postings[key] = posting
                state = parsed.status
                if parsed.search_links and not search_failed and not parsed.unresolved_links:
                    state = "parsed"
                if search_failed:
                    state = "needs_review"
                if state == "parsed" and extracted and not kept:
                    state = "filtered"
                statuses[state] = statuses.get(state, 0) + 1
                self._pending.append(ImportRecord(mailbox_key, identifier, hashlib.sha256(raw).hexdigest(), state, len(kept)))
            except Exception:
                # Failed reads stay unacknowledged and retry on the next run.
                read_errors += 1
        self._postings = list(postings.values())
        partial = more_pending or read_errors or statuses.get("needs_review", 0)
        return FetchResult(postings.values(), status="partial" if partial else "success", details={
            "fetched_messages": fetched, "message_statuses": statuses, "read_errors": read_errors,
            "more_messages_pending": more_pending, "description_incomplete": True,
            "parser_version": PARSER_VERSION, "cards_found": cards_found, "filtered_offers": filtered,
            "search_filters_applied": settings.apply_search_filters, "unread_only": settings.unread_only,
            "unresolved_links": unresolved, "providers": providers,
            "search_results": list({item["url"]: item for item in search_results}.values())[:20],
        })

    def after_store(self, db):
        for posting in self._postings:
            repo.refresh_email_posting(db, posting)
        acknowledge(db, self._pending)
        self._pending = []
