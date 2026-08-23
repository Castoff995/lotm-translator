"""Local-only HTTP boundary for the dedicated Pairwise Gold Reviewer."""
from __future__ import annotations

import json
import mimetypes
import secrets
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .service import PairwiseReviewError, PairwiseReviewWorkspace


STATIC_DIR = Path(__file__).with_name("static")


class PairwiseReviewHTTPServer(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], workspace: PairwiseReviewWorkspace) -> None:
        super().__init__(address, PairwiseReviewHandler)
        self.workspace = workspace
        self.review_token = secrets.token_urlsafe(32)


class PairwiseReviewHandler(BaseHTTPRequestHandler):
    server: PairwiseReviewHTTPServer

    def log_message(self, format: str, *args: object) -> None:
        print(f"[pairwise-review] {self.address_string()} {format % args}")

    def do_GET(self) -> None:
        route = urlparse(self.path).path
        if route == "/api/session":
            self._json(HTTPStatus.OK, self.server.workspace.snapshot())
            return
        if route == "/":
            value = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
            self._bytes(
                HTTPStatus.OK, value.replace("__REVIEW_TOKEN__", self.server.review_token).encode("utf-8"),
                "text/html; charset=utf-8",
            )
            return
        name = route.lstrip("/")
        if name in {"app.js", "styles.css"}:
            path = STATIC_DIR / name
            kind = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            self._bytes(HTTPStatus.OK, path.read_bytes(), f"{kind}; charset=utf-8")
            return
        self._json(HTTPStatus.NOT_FOUND, {"error": "Not found"})

    def do_POST(self) -> None:
        if self.headers.get("X-Review-Token") != self.server.review_token:
            self._json(HTTPStatus.FORBIDDEN, {"error": "Invalid local review token"})
            return
        try:
            payload = self._request_json()
            route = urlparse(self.path).path
            workspace = self.server.workspace
            if route == "/api/preview":
                result = workspace.preview(payload, payload["expected_revision"])
            elif route == "/api/disposition/preview":
                result = workspace.preview_disposition(
                    str(payload["side"]), str(payload["reason"]),
                    payload["expected_revision"], payload.get("note"),
                )
            elif route == "/api/rollback/preview":
                result = workspace.preview_rollback(
                    payload["keep_units"], payload["expected_revision"],
                )
            elif route in {
                "/api/confirm", "/api/disposition/confirm", "/api/rollback/confirm",
                "/api/correction/split/confirm", "/api/correction/merge/confirm",
                "/api/correction/disposition/confirm",
            }:
                operation = {
                    "/api/confirm": "confirm_unit",
                    "/api/disposition/confirm": "disposition",
                    "/api/rollback/confirm": "rollback",
                    "/api/correction/split/confirm": "split",
                    "/api/correction/merge/confirm": "merge",
                    "/api/correction/disposition/confirm": "edit_disposition",
                }[route]
                result = workspace.confirm_preview(
                    str(payload["preview_token"]), str(payload["request_id"]),
                    payload["expected_revision"], operation,
                )
            elif route == "/api/undo":
                result = workspace.undo(
                    payload["expected_revision"], str(payload["request_id"]),
                )
            elif route == "/api/bootstrap/use-remaining":
                result = workspace.use_remaining_bootstrap_span()
            elif route == "/api/correction/split/preview":
                result = workspace.preview_split(
                    str(payload["unit_id"]), dict(payload["first_counts"]),
                    payload["expected_revision"],
                )
            elif route == "/api/correction/merge/preview":
                result = workspace.preview_merge(
                    str(payload["unit_id"]), payload["expected_revision"],
                )
            elif route == "/api/correction/disposition/preview":
                result = workspace.preview_disposition_edit(
                    str(payload["paragraph_id"]), str(payload["reason"]), payload.get("note"),
                    payload["expected_revision"],
                )
            elif route == "/api/validate":
                result = workspace.finish()
            elif route == "/api/publish":
                result = workspace.publish(
                    explicit_confirmation=payload.get("confirm") is True,
                    expected_revision=payload["expected_revision"],
                    request_id=str(payload["request_id"]),
                )
            elif route == "/api/discard":
                result = workspace.discard(
                    payload["expected_revision"], str(payload["request_id"]),
                )
            else:
                self._json(HTTPStatus.NOT_FOUND, {"error": "Not found"})
                return
            self._json(HTTPStatus.OK, result)
        except (KeyError, TypeError, ValueError, PairwiseReviewError) as error:
            message = str(error)
            status = (
                HTTPStatus.CONFLICT
                if "revision conflict" in message or "preview" in message.lower()
                else HTTPStatus.BAD_REQUEST
            )
            self._json(status, {"error": message})

    def _request_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        value = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        if not isinstance(value, dict):
            raise ValueError("Request body must be a JSON object")
        return value

    def _json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        self._bytes(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _bytes(self, status: HTTPStatus, content: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)
