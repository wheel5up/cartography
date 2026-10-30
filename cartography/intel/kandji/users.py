import logging
from typing import Any

import neo4j
from requests import Session

from cartography.client.core.tx import load
from cartography.graph.job import GraphJob
from cartography.models.kandji.tenant import KandjiTenantSchema
from cartography.models.kandji.user import KandjiUserSchema
from cartography.util import timeit

logger = logging.getLogger(__name__)
_TIMEOUT = (60, 60)


@timeit
def get(kandji_base_uri: str, kandji_token: str) -> list[dict[str, Any]]:
    """
    Fetch all users from the Kandji user directory.

    /api/v1/users is cursor-paginated: each response is
    {"next": <url|null>, "previous": <url|null>, "results": [...]}.
    """
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {kandji_token}",
    }

    users: list[dict[str, Any]] = []
    session = Session()
    next_url: str | None = f"{kandji_base_uri}/api/v1/users"
    while next_url:
        response = session.get(next_url, headers=headers, timeout=_TIMEOUT)
        response.raise_for_status()
        result = response.json()
        users.extend(result.get("results", []))
        next_url = result.get("next")

    logger.debug("Kandji user count: %d", len(users))
    return users


@timeit
def transform(api_result: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for user in api_result:
        integration = user.get("integration") or {}
        result.append(
            {
                "id": user["id"],
                "email": user.get("email"),
                "name": user.get("name"),
                "active": user.get("active"),
                "archived": user.get("archived"),
                "department": user.get("department"),
                "job_title": user.get("job_title"),
                "device_count": user.get("device_count"),
                "integration_type": integration.get("type"),
                "created_at": user.get("created_at"),
                "updated_at": user.get("updated_at"),
            },
        )
    return result


@timeit
def load_users(
    neo4j_session: neo4j.Session,
    common_job_parameters: dict[str, Any],
    data: list[dict[str, Any]],
) -> None:
    tenant_id = common_job_parameters["TENANT_ID"]
    update_tag = common_job_parameters["UPDATE_TAG"]

    load(
        neo4j_session,
        KandjiTenantSchema(),
        [{"id": tenant_id}],
        lastupdated=update_tag,
    )

    load(
        neo4j_session,
        KandjiUserSchema(),
        data,
        lastupdated=update_tag,
        TENANT_ID=tenant_id,
    )


def cleanup(
    neo4j_session: neo4j.Session,
    common_job_parameters: dict[str, Any],
) -> None:
    GraphJob.from_node_schema(KandjiUserSchema(), common_job_parameters).run(
        neo4j_session,
    )


@timeit
def sync(
    neo4j_session: neo4j.Session,
    kandji_base_uri: str,
    kandji_token: str,
    common_job_parameters: dict[str, Any],
) -> None:
    users = get(kandji_base_uri=kandji_base_uri, kandji_token=kandji_token)
    formatted_users = transform(users)
    load_users(neo4j_session, common_job_parameters, formatted_users)
    cleanup(neo4j_session, common_job_parameters)
