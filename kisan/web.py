"""Browser page and token endpoint so the same agent can be tried before the phone number is live."""

from __future__ import annotations

import asyncio
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

from dotenv import load_dotenv

from kisan.db import normalize_phone

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "web" / "index.html"


def main() -> None:
    port = int(os.environ.get("WEB_PORT", "8787"))
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Open http://127.0.0.1:{port}", flush=True)
    server.serve_forever()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/token":
            self._token(parse_qs(parsed.query))
            return
        if parsed.path in ("/", "/index.html"):
            body = PAGE.read_bytes()
            self._send(200, body, "text/html; charset=utf-8")
            return
        self._send(404, b"Not found", "text/plain")

    def log_message(self, fmt: str, *args) -> None:
        print(fmt % args)

    def _token(self, query: dict[str, list[str]]) -> None:
        raw_phone = (query.get("phone") or [""])[0]
        phone = normalize_phone(raw_phone)
        if not phone.startswith("+91"):
            self._send(400, _json({"error": "Enter a 10-digit Indian mobile number."}), "application/json")
            return
        url = os.environ.get("LIVEKIT_URL", "")
        key = os.environ.get("LIVEKIT_API_KEY", "")
        secret = os.environ.get("LIVEKIT_API_SECRET", "")
        if not (url and key and secret):
            self._send(
                503,
                _json({"error": "Set LIVEKIT_URL, LIVEKIT_API_KEY, and LIVEKIT_API_SECRET in .env."}),
                "application/json",
            )
            return
        try:
            token, room = asyncio.run(_issue_token(url, key, secret, phone))
        except Exception as exc:
            self._send(502, _json({"error": str(exc)}), "application/json")
            return
        self._send(200, _json({"token": token, "url": url, "room": room, "phone": phone}), "application/json")

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


async def _issue_token(url: str, key: str, secret: str, phone: str) -> tuple[str, str]:
    from livekit import api

    room = f"kisan-{phone.lstrip('+')}-{uuid4().hex[:8]}"
    metadata = json.dumps({"phone": phone})
    token = (
        api.AccessToken(key, secret)
        .with_identity(phone)
        .with_name(phone)
        .with_metadata(metadata)
        .with_attributes({"caller_phone": phone})
        .with_grants(
            api.VideoGrants(
                room_join=True,
                room=room,
                can_publish=True,
                can_subscribe=True,
            )
        )
        .to_jwt()
    )
    client = api.LiveKitAPI(url, key, secret)
    try:
        await client.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(
                agent_name=os.environ.get("AGENT_NAME", "kisan-agent"),
                room=room,
                metadata=metadata,
            )
        )
    finally:
        await client.aclose()
    return token, room


def _json(payload: dict) -> bytes:
    return json.dumps(payload).encode("utf-8")


if __name__ == "__main__":
    main()
