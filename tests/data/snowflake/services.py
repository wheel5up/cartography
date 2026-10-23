"""Raw Snowflake service payloads and their sub-listings.

``SNOWFLAKE_SERVICES`` is shaped as the per-schema listing returns it, and the three
``*_BY_SERVICE`` mappings as the per-service containers, endpoints and roles listings
return them.
"""

from typing import Any

from tests.data.snowflake.image_repositories import SNOWFLAKE_MONORAIL_IMAGE_DIGEST

SNOWFLAKE_SERVICES: list[dict[str, Any]] = [
    {
        "name": "MONORAIL_TELEMETRY",
        "database_name": "SPRINGFIELD",
        "schema_name": "NUCLEAR_PLANT",
        "status": "RUNNING",
        "compute_pool": "MONORAIL_POOL",
        "spec_digest": "sha256:aaaa",
        "dns_name": "monorail-telemetry.springfield.nuclear-plant.svc.spcs.internal",
        "current_instances": 2,
        "target_instances": 2,
        "min_instances": 1,
        "max_instances": 3,
        "auto_resume": True,
        "external_access_integrations": ["DUFF_API_ACCESS"],
        "query_warehouse": "REACTOR_WH",
        "is_job": False,
        "is_upgrading": False,
        "owner": "SYSADMIN",
        "comment": "Streams monorail telemetry into the plant tables",
        "created_on": "2026-08-03T18:00:00.000+00:00",
    },
    # A job service with no query warehouse and no external access.
    {
        "name": "DONUT_BACKFILL",
        "database_name": "SPRINGFIELD",
        "schema_name": "NUCLEAR_PLANT",
        "status": "DONE",
        "compute_pool": "MONORAIL_POOL",
        "spec_digest": "sha256:bbbb",
        "dns_name": None,
        "current_instances": 0,
        "target_instances": 0,
        "min_instances": 1,
        "max_instances": 1,
        "auto_resume": False,
        "external_access_integrations": [],
        "query_warehouse": None,
        "is_job": True,
        "is_upgrading": False,
        "owner": "HOMER",
        "comment": None,
        "created_on": "2026-08-03T18:02:00.000+00:00",
    },
    # A suspended service. Per Snowflake's documented SHOW SERVICE CONTAINERS
    # IN SERVICE contract, a service with no containers currently running
    # (SUSPENDED or PENDING) still returns exactly one row with
    # container_name/instance_id and all container-specific fields as NULL --
    # see SNOWFLAKE_CONTAINERS_BY_SERVICE below.
    {
        "name": "SQUISHEE_DISPENSER",
        "database_name": "SPRINGFIELD",
        "schema_name": "NUCLEAR_PLANT",
        "status": "SUSPENDED",
        "compute_pool": "MONORAIL_POOL",
        "spec_digest": "sha256:cccc",
        "dns_name": None,
        "current_instances": 0,
        "target_instances": 0,
        "min_instances": 1,
        "max_instances": 1,
        "auto_resume": True,
        "external_access_integrations": [],
        "query_warehouse": None,
        "is_job": False,
        "is_upgrading": False,
        "owner": "APU",
        "comment": None,
        "created_on": "2026-08-03T18:03:00.000+00:00",
    },
]

SNOWFLAKE_CONTAINERS_BY_SERVICE: dict[str, Any] = {
    "MONORAIL_TELEMETRY": [
        {
            "container_name": "telemetry",
            "instance_id": 0,
            "status": "READY",
            "image_name": (
                "/springfield/nuclear_plant/plant_images/monorail-telemetry:latest"
            ),
            "image_digest": SNOWFLAKE_MONORAIL_IMAGE_DIGEST,
            "restart_count": 0,
            "start_time": "2026-08-03T18:01:00.000+00:00",
            "message": "Running",
        },
        # The same container name on a second instance, which is why the instance id
        # is part of the node key.
        {
            "container_name": "telemetry",
            "instance_id": 1,
            "status": "READY",
            "image_name": (
                "/springfield/nuclear_plant/plant_images/monorail-telemetry:latest"
            ),
            "image_digest": SNOWFLAKE_MONORAIL_IMAGE_DIGEST,
            "restart_count": 2,
            "start_time": "2026-08-03T18:01:30.000+00:00",
            "message": "Running",
        },
    ],
    "DONUT_BACKFILL": [],
    # The documented SUSPENDED/PENDING placeholder row: every field other than
    # service-level identity (not present in this per-container row at all)
    # comes back NULL. transform() must skip this row rather than building a
    # bogus container node from it (or crashing on sf_fqn(None)).
    "SQUISHEE_DISPENSER": [
        {
            "container_name": None,
            "instance_id": None,
            "status": None,
            "image_name": None,
            "image_digest": None,
            "restart_count": None,
            "start_time": None,
            "message": None,
        },
    ],
}

SNOWFLAKE_ENDPOINTS_BY_SERVICE: dict[str, Any] = {
    "MONORAIL_TELEMETRY": [
        # A public endpoint: Snowflake fronts it with an internet-reachable ingress.
        {
            "name": "dashboard",
            "port": 8080,
            "port_range": None,
            "protocol": "HTTP",
            "is_public": True,
            "ingress_url": "monorail-dashboard-ab12345.snowflakecomputing.app",
        },
        {
            "name": "metrics",
            "port": 9090,
            "port_range": None,
            "protocol": "HTTP",
            "is_public": False,
            "ingress_url": None,
        },
    ],
    "DONUT_BACKFILL": [],
    "SQUISHEE_DISPENSER": [],
}

SNOWFLAKE_SERVICE_ROLES_BY_SERVICE: dict[str, Any] = {
    "MONORAIL_TELEMETRY": [
        {
            "name": "DASHBOARD_VIEWER",
            "comment": "Grants access to the dashboard endpoint",
        },
    ],
    "DONUT_BACKFILL": [],
    "SQUISHEE_DISPENSER": [],
}
