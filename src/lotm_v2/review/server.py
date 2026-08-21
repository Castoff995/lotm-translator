"""Local-only HTTP interface for the Phase 2 human review service."""
from __future__ import annotations

import json
import mimetypes
import secrets
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ..domain import Language, Paragraph
from ..glossary import GlossaryDuplicateError, GlossaryService
from ..hints import HintService, bundle_to_dict
from .service import ReviewError, ReviewSession


STATIC_DIR = Path(__file__).with_name("static")


class ReviewHTTPServer(ThreadingHTTPServer):
    def __init__(
        self, address: tuple[str, int], session: ReviewSession,
        hint_service: HintService | None = None,
        glossary_service: GlossaryService | None = None,
    ) -> None:
        super().__init__(address, ReviewHandler)
        self.session = session
        self.hints = hint_service or HintService(session.root / "data" / "cache" / "v2" / "hints")
        self.glossary = glossary_service or GlossaryService(
            session.root / "data" / "glossary" / "v2" / f"{session.gold.chapter.work_id}.json",
            session.gold.chapter.work_id,
        )
        self.review_token = secrets.token_urlsafe(32)

    def zh_paragraph(self, paragraph_id: str) -> Paragraph:
        chapter = self.session.chapter_by_language[Language.ZH]
        paragraph = next((item for item in chapter.paragraphs if str(item.id) == paragraph_id), None)
        if paragraph is None:
            raise ReviewError("Hints require a Paragraph ID from the displayed Chinese source")
        return paragraph

    def glossary_context(self, payload: dict[str, Any]) -> tuple[Paragraph, int, int]:
        paragraph = self.zh_paragraph(str(payload["paragraph_id"]))
        start, end = int(payload["start_offset"]), int(payload["end_offset"])
        if start < 0 or end <= start or end > len(paragraph.normalized_text):
            raise ReviewError("Glossary offsets are outside the Chinese Paragraph")
        selected = paragraph.normalized_text[start:end]
        if selected != str(payload["zh_term"]):
            raise ReviewError("Glossary Chinese term must exactly match the selected Paragraph span")
        return paragraph, start, end


class ReviewHandler(BaseHTTPRequestHandler):
    server: ReviewHTTPServer

    def log_message(self, format: str, *args: object) -> None:
        print(f"[review] {self.address_string()} {format % args}")

    def do_GET(self) -> None:
        route = urlparse(self.path).path
        if route == "/api/session":
            self._json(HTTPStatus.OK, self.server.session.snapshot())
            return
        if route == "/":
            content = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
            content = content.replace("__REVIEW_TOKEN__", self.server.review_token)
            self._bytes(HTTPStatus.OK, content.encode("utf-8"), "text/html; charset=utf-8")
            return
        safe_name = route.lstrip("/")
        if safe_name in {"app.js", "styles.css"}:
            path = STATIC_DIR / safe_name
            content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            self._bytes(HTTPStatus.OK, path.read_bytes(), f"{content_type}; charset=utf-8")
            return
        self._json(HTTPStatus.NOT_FOUND, {"error": "Not found"})

    def do_POST(self) -> None:
        if self.headers.get("X-Review-Token") != self.server.review_token:
            self._json(HTTPStatus.FORBIDDEN, {"error": "Invalid local review token"})
            return
        try:
            payload = self._request_json()
            route = urlparse(self.path).path
            if route == "/api/preview":
                result = self.server.session.preview(payload)
            elif route == "/api/hints":
                paragraph = self.server.zh_paragraph(str(payload["paragraph_id"]))
                result = bundle_to_dict(self.server.hints.get(
                    str(paragraph.id), paragraph.normalized_text,
                ))
                result["glossary_occurrences"] = self.server.glossary.occurrences(
                    paragraph.normalized_text,
                )
            elif route == "/api/glossary/list":
                result = self.server.glossary.list(
                    str(payload.get("query", "")), payload.get("status"),
                )
            elif route == "/api/glossary/preview":
                paragraph, start, end = self.server.glossary_context(payload)
                result = self.server.glossary.preview(
                    str(payload["zh_term"]), str(payload["en_term"]),
                    paragraph.chapter.number, str(paragraph.id), start, end,
                    payload.get("notes"),
                )
            elif route == "/api/glossary/add":
                paragraph, start, end = self.server.glossary_context(payload)
                entry = self.server.glossary.add(
                    str(payload["zh_term"]), str(payload["en_term"]),
                    paragraph.chapter.number, str(paragraph.id), start, end,
                    payload.get("notes"),
                )
                result = {"saved": True, "entry": self.server.glossary.entry_payload(entry)}
            elif route == "/api/glossary/alias":
                entry = self.server.glossary.add_alias(
                    str(payload["zh_term"]), str(payload["language"]), str(payload["alias"]),
                )
                result = {"saved": True, "entry": self.server.glossary.entry_payload(entry)}
            elif route == "/api/confirm":
                result = self.server.session.confirm(payload)
            elif route == "/api/disposition":
                result = self.server.session.disposition(
                    str(payload["language"]), str(payload["reason"]), payload.get("note"),
                )
            elif route == "/api/undo":
                result = self.server.session.undo()
            elif route == "/api/rollback":
                result = self.server.session.rollback(int(payload["keep_units"]))
            elif route == "/api/boundary":
                result = self.server.session.set_boundary(
                    str(payload["after"]), str(payload["decision"]), payload.get("note"),
                )
            elif route == "/api/finish":
                result = self.server.session.finish()
            else:
                self._json(HTTPStatus.NOT_FOUND, {"error": "Not found"})
                return
            self._json(HTTPStatus.OK, result)
        except GlossaryDuplicateError as error:
            self._json(HTTPStatus.CONFLICT, {
                "error": str(error), "existing": self.server.glossary.entry_payload(error.entry),
            })
        except (ReviewError, ValueError, KeyError, TypeError) as error:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
        except Exception as error:  # pragma: no cover - defensive HTTP boundary
            self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"Internal error: {error}"})

    def _request_json(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError) as error:
            raise ReviewError("Request body must be valid JSON") from error
        if not isinstance(payload, dict):
            raise ReviewError("Request body must be a JSON object")
        return payload

    def _json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        self._bytes(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _bytes(self, status: HTTPStatus, content: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'")
        self.end_headers()
        self.wfile.write(content)
