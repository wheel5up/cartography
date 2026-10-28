import json
import logging
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
from threading import Thread

import pytest
import requests

from cartography.intel.snowflake.schemas import get as get_schemas
from cartography.intel.snowflake.schemas import get_database_schemas
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


def test_database_schema_listing_400_skips_only_that_database(caplog):
    """A database that cannot be addressed at all (400) is skipped, not fatal.

    Confirmed against a real account (2026-10-16): Snowflake's auto-generated
    `DROPPED_USER$<user>@<domain>_<epoch>` placeholder databases - created when
    a user with owned objects is dropped - answer 400/002043 ("object does not
    exist, or operation cannot be performed") on this exact endpoint, not
    403/404, because the embedded `@`/`.`/`$` characters break REST identifier
    quoting rather than signal a real USAGE gap. Readable databases alongside
    it must still be collected, and the walk must be marked incomplete so
    cleanup does not run on a partial result.
    """
    # Arrange
    caplog.set_level(logging.WARNING, logger="cartography.intel.snowflake.schemas")
    requested_paths = []
    # sf_path_segment()'s own quoting of this (unquoted, as Snowflake reports it)
    # database name produces the embedded-quote path actually requested -
    # confirmed directly against a real account's DROPPED_USER$... database name
    # (2026-10-16), which contains '@'/'.'/'$' and is therefore quoted.
    blocked_path = (
        "/api/v2/databases/%22DROPPED_USER%24AHENDERSON%40G2.COM_123%22/schemas"
    )

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requested_paths.append(self.path)
            if self.path == blocked_path:
                _respond_json(
                    self,
                    {
                        "code": "002043",
                        "message": "\nobject does not exist, or operation "
                        "cannot be performed.",
                    },
                    status=400,
                )
            else:
                _respond_json(self, [{"name": "PUBLIC", "database_name": "READABLE"}])

        def log_message(self, format, *args):
            pass

    server = _serve(Handler)
    client = _build_client(server.server_port)
    try:
        # Act
        result, complete = get_schemas(
            client,
            [
                {"name": "DROPPED_USER$AHENDERSON@G2.COM_123"},
                {"name": "READABLE"},
            ],
        )
    finally:
        _shutdown(server)

    # Assert
    assert [row["name"] for row in result] == ["PUBLIC"]
    assert complete is False
    assert requested_paths == [
        blocked_path,
        "/api/v2/databases/READABLE/schemas",
    ]
    assert (
        "Snowflake error 002043:" in caplog.text
        and "object does not exist" in caplog.text
    )


@pytest.mark.parametrize("status", [403, 404])
def test_database_schema_listing_403_404_skips_only_that_database(status):
    """Pre-existing behavior (unchanged): a permission/not-found database is
    also skipped, not fatal - confirming the 400 addition above is additive,
    not a replacement of the original 403/404 tolerance.
    """
    # Arrange
    requested_paths = []
    blocked_path = "/api/v2/databases/UNREADABLE/schemas"

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requested_paths.append(self.path)
            if self.path == blocked_path:
                _respond_json(self, {"message": "forbidden"}, status=status)
            else:
                _respond_json(self, [{"name": "PUBLIC", "database_name": "READABLE"}])

        def log_message(self, format, *args):
            pass

    server = _serve(Handler)
    client = _build_client(server.server_port)
    try:
        # Act
        result, complete = get_schemas(
            client,
            [{"name": "UNREADABLE"}, {"name": "READABLE"}],
        )
    finally:
        _shutdown(server)

    # Assert
    assert [row["name"] for row in result] == ["PUBLIC"]
    assert complete is False


def test_database_schema_listing_409_still_fails():
    """A status outside (400, 403, 404) and not the narrow 401/003001 case is
    not a known "unaddressable database" or "no access" signal and must still
    fail the whole sync - mirrors the schema-level precedent in
    test_util.py's test_schema_listing_still_fails_on_other_errors.
    """

    # Arrange
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
            get_database_schemas(client, "SOME_DB")
    finally:
        _shutdown(server)


def test_database_schema_listing_401_insufficient_privileges_skips(caplog):
    """A 401 whose Snowflake error code is specifically 003001
    ("insufficient privileges ... must have usage granted") is an ordinary
    permission-denied result, not an auth failure, and is skipped the same
    way a 403 would be.

    Confirmed against a real account (2026-10-16): one
    `DROPPED_USER$<user>_<epoch>` placeholder database (the unquoted-looking
    form with no `@`/domain suffix, as distinct from the quoted form that
    triggers the 400/002043 case) answered exactly this 401/003001 shape.
    """
    # Arrange
    caplog.set_level(logging.WARNING, logger="cartography.intel.snowflake.schemas")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            _respond_json(
                self,
                {
                    "code": "003001",
                    "message": (
                        "sql access control error:\ninsufficient privileges to "
                        "operate on database 'dropped_user$test_user_123'. your "
                        "primary role is_cartography or one of your secondary "
                        "roles must have usage granted on database "
                        "dropped_user$test_user_123."
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
        result = get_database_schemas(client, "DROPPED_USER$TEST_USER_123")
    finally:
        _shutdown(server)

    # Assert
    assert result is None
    assert "Snowflake error 003001:" in caplog.text


def test_database_schema_listing_401_other_reason_still_fails():
    """A 401 that is NOT the specific 003001 insufficient-privileges case must
    still fail the whole sync. This is the safety boundary the narrow check
    exists for: Snowflake documents 401 for real authentication failures too
    (390144 "JWT token is invalid.", 390432 "Network policy is required."),
    which would make EVERY database answer 401 if the credential itself were
    broken - a blanket 401 skip would silently "complete" a sync that read
    nothing. Confirmed by checking the error code, not just the HTTP status.
    """

    # Arrange
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
            get_database_schemas(client, "SOME_DB")
    finally:
        _shutdown(server)


def test_database_schema_listing_401_without_json_body_still_fails():
    """A 401 with a non-JSON or empty body cannot be confirmed as the
    insufficient-privileges case and must still fail loudly rather than be
    assumed safe to skip.
    """

    # Arrange
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = b"not json"
            self.send_response(401)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            pass

    server = _serve(Handler)
    client = _build_client(server.server_port)
    try:
        # Act / Assert
        with pytest.raises(requests.HTTPError):
            get_database_schemas(client, "SOME_DB")
    finally:
        _shutdown(server)


def test_walkable_omits_skipped_schemas_case_insensitively():
    from cartography.intel.snowflake.schemas import walkable

    schemas = [
        {"database_name": "SNOWFLAKE_LEARNING_DB", "name": '"GMCFAUL@G2.COM_tutorial"'},
        {"database_name": "SNOWFLAKE_LEARNING_DB", "name": "ADS_PERSONALISATION_DATA"},
        {"database_name": "GDC", "name": "SIGMA"},
        {"database_name": "GDC", "name": "LOOKER_SCRATCH"},
    ]

    kept = walkable(schemas, frozenset({"snowflake_learning_db.*@*", "gdc.sigma"}))

    assert [(s["database_name"], s["name"]) for s in kept] == [
        ("SNOWFLAKE_LEARNING_DB", "ADS_PERSONALISATION_DATA"),
        ("GDC", "LOOKER_SCRATCH"),
    ]
    # No patterns is a no-op that returns the same list.
    assert walkable(schemas, frozenset()) is schemas
