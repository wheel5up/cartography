"""Snowflake schemas, the namespaces every table, view and stream lives in.

Schemas are listed per database. A collector role without ``USAGE`` on one
database must not fail the whole sync, so a 403 or 404 on a single database is
recorded as incomplete and the caller skips cleanup instead of deleting schemas it
merely failed to re-read. Snowflake also answers 400 for a database that cannot be
addressed at all - for example an auto-generated ``DROPPED_USER$...`` placeholder
database, whose name embeds characters (``@``, ``.``, ``$``) that break identifier
quoting on this endpoint - which is the same "object does not exist, or operation
cannot be performed" (Snowflake error 002043) class of failure
``list_schema_objects`` already tolerates for schema-scoped listings one level
down. See that function's docstring in ``util.py`` for the sibling case.

A narrower third case is also tolerated: a 401 whose Snowflake error code is
specifically 003001 ("insufficient privileges ... must have USAGE granted"),
which is an ordinary permission-denied result that happens to map to 401
instead of 403 on this one endpoint shape. This is deliberately NOT a blanket
401 skip - Snowflake's own docs (390144 "JWT token is invalid", 390432
"Network policy is required.") document 401 as the status for real
authentication failures too. A bad credential would make every database
answer 401; blanket-skipping all of them would silently "complete" a sync
that read nothing, exactly the kind of failure this repo's own STATUS.md
documents a real production incident for. Checking the specific error code
keeps the skip scoped to the one confirmed-safe case.
"""

import logging
from typing import Any

import neo4j
import requests

from cartography.client.core.tx import load
from cartography.graph.job import GraphJob
from cartography.intel.snowflake.util import http_error_detail
from cartography.intel.snowflake.util import iso_to_datetime
from cartography.intel.snowflake.util import sf_fqn
from cartography.intel.snowflake.util import sf_id
from cartography.intel.snowflake.util import sf_path_segment
from cartography.intel.snowflake.util import skip_or_raise_http
from cartography.intel.snowflake.util import SnowflakeClient
from cartography.models.snowflake.schema import SnowflakeSchemaSchema
from cartography.util import timeit

logger = logging.getLogger(__name__)

# Statuses on which a single database is skipped (its schema listing is
# incomplete) rather than failing the whole Snowflake sync. 403/404 are the role
# genuinely lacking USAGE or the database no longer existing; 400 is Snowflake
# reporting an unaddressable database - confirmed via a real account
# (2026-10-16): every `DROPPED_USER$<user>@<domain>_<epoch>` placeholder database
# Snowflake auto-generates on user drop returns 400/002043 ("object does not
# exist, or operation cannot be performed") on this exact endpoint, not 403/404,
# because the embedded `@`/`.`/`$` characters in its name break REST identifier
# quoting rather than signal a real permission gap. Matches the schema-level
# precedent in `list_schema_objects`/`SCHEMA_LISTING_SKIPPABLE_STATUSES` one
# level down - same root cause, different addressing layer. 401 is
# DELIBERATELY excluded from this blanket set - see
# _is_skippable_insufficient_privileges_401 for the narrower, code-checked
# case that covers it instead.
_DATABASE_SCHEMA_LISTING_SKIPPABLE_STATUSES = (400, 403, 404)

# Snowflake's error code for "the connecting role lacks USAGE on this
# database" - confirmed via a real account (2026-10-16): one
# `DROPPED_USER$<user>_<epoch>` placeholder database (no `@`/domain suffix,
# unlike the quoted form that triggers the 400 case above) answered
# `401 {"code": "003001", "message": "sql access control error:\ninsufficient
# privileges to operate on database '...'. your primary role ... must have
# usage granted on database ..."}` - a real, ordinary permission-denied
# result on this account, not an auth failure.
_INSUFFICIENT_PRIVILEGES_ERROR_CODE = "003001"


def _is_skippable_insufficient_privileges_401(error: requests.HTTPError) -> bool:
    """Whether a 401 is Snowflake's USAGE-privilege error, not an auth failure.

    Deliberately narrow: checks the Snowflake-specific error code in the
    response body, not just the HTTP status. See this module's docstring for
    why a blanket 401 skip is unsafe (390144 "JWT token is invalid" and 390432
    "Network policy is required." are real upstream-documented 401 cases that
    must still fail the sync loudly).
    """
    response = error.response
    if response is None or response.status_code != 401:
        return False
    try:
        body = response.json()
    except ValueError:
        return False
    return body.get("code") == _INSUFFICIENT_PRIVILEGES_ERROR_CODE


@timeit
def get_database_schemas(
    client: SnowflakeClient, database_name: str
) -> list[dict[str, Any]] | None:
    """Schemas of one database, or None when it cannot be read at all."""
    try:
        return client.list_all(
            f"/api/v2/databases/{sf_path_segment(database_name)}/schemas"
        )
    except requests.HTTPError as error:
        if not _is_skippable_insufficient_privileges_401(error):
            skip_or_raise_http(error, *_DATABASE_SCHEMA_LISTING_SKIPPABLE_STATUSES)
        logger.warning(
            "Cannot list schemas of Snowflake database %s (%s); its contents "
            "will be missing from the graph.",
            database_name,
            http_error_detail(error),
        )
        return None


@timeit
def get(
    client: SnowflakeClient, databases: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], bool]:
    """Return every readable schema plus whether every database could be read."""
    schemas: list[dict[str, Any]] = []
    complete = True
    for database in databases:
        rows = get_database_schemas(client, database["name"])
        if rows is None:
            complete = False
            continue
        schemas.extend(rows)
    return schemas, complete


def transform(schemas: list[dict[str, Any]], account_id: str) -> list[dict[str, Any]]:
    transformed: list[dict[str, Any]] = []
    for schema in schemas:
        database_name = schema["database_name"]
        name = schema["name"]
        qualified_name = sf_fqn(database_name, name)
        external_volume = schema.get("external_volume") or None
        transformed.append(
            {
                "id": sf_id(account_id, "schema", qualified_name),
                "name": name,
                "qualified_name": qualified_name,
                "database_name": database_name,
                "parent_database_id": sf_id(
                    account_id, "database", sf_fqn(database_name)
                ),
                "kind": schema.get("kind"),
                "managed_access": schema.get("managed_access"),
                "owner": schema.get("owner"),
                "owner_role_type": schema.get("owner_role_type"),
                "comment": schema.get("comment"),
                "options": schema.get("options"),
                "retention_time": schema.get("retention_time"),
                "external_volume": external_volume,
                # Null when the schema has no default volume, which suppresses the
                # DEFAULT_EXTERNAL_VOLUME edge instead of pointing it at nothing.
                "external_volume_id": (
                    sf_id(account_id, "external_volume", sf_fqn(external_volume))
                    if external_volume
                    else None
                ),
                "catalog": schema.get("catalog") or None,
                "created_on": iso_to_datetime(schema.get("created_on")),
                "dropped_on": iso_to_datetime(schema.get("dropped_on")),
            },
        )
    return transformed


def load_schemas(
    neo4j_session: neo4j.Session,
    schemas: list[dict[str, Any]],
    account_id: str,
    update_tag: int,
) -> None:
    load(
        neo4j_session,
        SnowflakeSchemaSchema(),
        schemas,
        lastupdated=update_tag,
        ACCOUNT_ID=account_id,
    )


def cleanup(neo4j_session: neo4j.Session, common_job_parameters: dict) -> None:
    GraphJob.from_node_schema(SnowflakeSchemaSchema(), common_job_parameters).run(
        neo4j_session
    )


@timeit
def sync(
    neo4j_session: neo4j.Session,
    client: SnowflakeClient,
    databases: list[dict[str, Any]],
    common_job_parameters: dict,
) -> tuple[list[dict[str, Any]], bool]:
    """Sync Snowflake schemas and return them for the object-level syncs to walk.

    Returns ``(schemas, complete)``, where ``complete`` is False when any database
    could not be listed.
    """
    raw_schemas, complete = get(client, databases)
    schemas = transform(raw_schemas, client.account_id)
    logger.info(
        "Loading %d Snowflake schemas for account %s.", len(schemas), client.account_id
    )
    load_schemas(
        neo4j_session, schemas, client.account_id, common_job_parameters["UPDATE_TAG"]
    )
    if not complete:
        logger.warning(
            "Some Snowflake databases could not be listed; skipping schema cleanup "
            "so still-valid schemas are not deleted.",
        )
    return schemas, complete
