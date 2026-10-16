import json
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
from threading import Thread

import pytest
import requests

from cartography.intel.snowflake.services import get_service_sublisting
from cartography.intel.snowflake.util import SnowflakeClient

TEST_ACCOUNT = "MYORG.MYACCT"


def _build_client(port: int) -> SnowflakeClient:
    """A PAT client pointed at a local test server instead of Snowflake."""
    client = SnowflakeClient(TEST_ACCOUNT, "svc", pat="test-pat")
    client.host = f"http://127.0.0.1:{port}"
    client._session.mount("http://", client._session.adapters["https://"])
    return client


def _serve(handler: type[BaseHTTPRequestHandler]) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    server._thread = thread  # type: ignore[attr-defined]
    return server


def _shutdown(server: ThreadingHTTPServer) -> None:
    server.shutdown()
    server.server_close()
    server._thread.join()  # type: ignore[attr-defined]


def _respond_json(
    handler: BaseHTTPRequestHandler, payload: object, status: int = 200, **headers: str
):
    body = json.dumps(payload).encode()
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    for name, value in headers.items():
        handler.send_header(name, value)
    handler.end_headers()
    handler.wfile.write(body)


def test_service_sublisting_401_insufficient_privileges_skips():
    """A 401 whose Snowflake error code is specifically 003001
    ("insufficient privileges ... must have MONITOR granted on service ...")
    is an ordinary permission-denied result, not an auth failure, and is
    skipped rather than crashing the whole Snowflake sync.

    Confirmed against a real account (2026-10-16): a service backing a
    Streamlit-in-Snowflake app answered exactly this 401/003001 shape on its
    `containers` sub-listing, even though the connecting role could already
    read every surrounding table/view/schema fine - SERVICE objects only
    support the MONITOR/OPERATE privilege pair (no USAGE/REFERENCES
    equivalent this org's existing grants happened to cover).
    """

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            _respond_json(
                self,
                {
                    "code": "003001",
                    "message": (
                        "sql access control error:\ninsufficient privileges to "
                        "operate on service 'my_service'. your primary role "
                        "is_cartography or one of your secondary roles must have "
                        "monitor granted on service prod.app.my_service."
                    ),
                },
                status=401,
            )

        def log_message(self, format, *args):
            pass

    server = _serve(Handler)
    client = _build_client(server.server_port)
    try:
        # Act
        result = get_service_sublisting(
            client, "PROD", "APP", "MY_SERVICE", "containers"
        )
    finally:
        _shutdown(server)

    # Assert
    assert result is None


def test_service_sublisting_401_other_reason_still_fails():
    """A 401 that is NOT the specific 003001 insufficient-privileges case must
    still fail the whole sync - the same safety boundary schemas.py's
    identical case relies on (a broken credential would make every
    sub-listing answer 401; blanket-skipping would silently "complete" a sync
    that read nothing).
    """

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            _respond_json(
                self,
                {"code": "390144", "message": "JWT token is invalid. null"},
                status=401,
            )

        def log_message(self, format, *args):
            pass

    server = _serve(Handler)
    client = _build_client(server.server_port)
    try:
        # Act / Assert
        with pytest.raises(requests.HTTPError):
            get_service_sublisting(client, "PROD", "APP", "MY_SERVICE", "containers")
    finally:
        _shutdown(server)


@pytest.mark.parametrize("status", [400, 403, 404])
def test_service_sublisting_schema_listing_statuses_still_skip(status):
    """Pre-existing behavior (unchanged): the shared
    SCHEMA_LISTING_SKIPPABLE_STATUSES (400, 403, 404) still skip - confirming
    the new 401/003001 handling is additive, not a replacement.
    """

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            _respond_json(self, {"message": "forbidden"}, status=status)

        def log_message(self, format, *args):
            pass

    server = _serve(Handler)
    client = _build_client(server.server_port)
    try:
        # Act
        result = get_service_sublisting(client, "PROD", "APP", "MY_SERVICE", "roles")
    finally:
        _shutdown(server)

    # Assert
    assert result is None


def test_service_sublisting_409_still_fails():
    """A status outside (400, 403, 404) and not the narrow 401/003001 case
    must still fail the whole sync.
    """

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            _respond_json(self, {"message": "boom"}, status=409)

        def log_message(self, format, *args):
            pass

    server = _serve(Handler)
    client = _build_client(server.server_port)
    try:
        # Act / Assert
        with pytest.raises(requests.HTTPError):
            get_service_sublisting(client, "PROD", "APP", "MY_SERVICE", "endpoints")
    finally:
        _shutdown(server)
