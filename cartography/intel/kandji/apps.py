import logging
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import neo4j
import requests
from requests import Session

from cartography.client.core.tx import load
from cartography.graph.job import GraphJob
from cartography.models.kandji.app import KandjiAppSchema
from cartography.models.kandji.app import KandjiAppVersionSchema
from cartography.util import timeit

logger = logging.getLogger(__name__)
_TIMEOUT = (60, 60)

# Kandji allows 50 req/s and 10,000 req/hour per token. 8 workers measured
# ~24 req/s against a real tenant with no 429s; backoff below covers bursts.
_MAX_WORKERS = 8
_MAX_RETRIES = 5
_BACKOFF_BASE_SECONDS = 1.0


def _get_device_apps(
    session: Session,
    kandji_base_uri: str,
    headers: dict[str, str],
    device_id: str,
) -> list[dict[str, Any]]:
    """Fetch installed apps for one device, retrying on 429 and 5xx."""
    url = f"{kandji_base_uri}/api/v1/devices/{device_id}/apps"
    for attempt in range(_MAX_RETRIES + 1):
        response = session.get(url, headers=headers, timeout=_TIMEOUT)
        if response.status_code == 429 or response.status_code >= 500:
            if attempt == _MAX_RETRIES:
                response.raise_for_status()
            retry_after = response.headers.get("Retry-After")
            delay = (
                float(retry_after)
                if retry_after and retry_after.replace(".", "", 1).isdigit()
                else _BACKOFF_BASE_SECONDS * (2**attempt)
            )
            logger.debug(
                "Kandji apps %s for device %s; retrying in %.1fs",
                response.status_code,
                device_id,
                delay,
            )
            time.sleep(delay)
            continue
        if response.status_code == 404:
            # Device removed between the device list and this call.
            logger.debug("Kandji device %s not found when fetching apps", device_id)
            return []
        response.raise_for_status()
        return response.json().get("apps", [])
    return []  # unreachable; keeps mypy happy


@timeit
def get(
    kandji_base_uri: str,
    kandji_token: str,
    device_ids: list[str],
) -> dict[str, list[dict[str, Any]]]:
    """
    Fetch installed apps for each device. Kandji has no fleet-wide app
    inventory endpoint, so this is one call per device, run in parallel.

    :return: {device_id: [app, ...]}
    """
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {kandji_token}",
    }
    session = Session()
    adapter = requests.adapters.HTTPAdapter(pool_maxsize=_MAX_WORKERS)
    session.mount("https://", adapter)
    session.mount("http://", adapter)

    def fetch(device_id: str) -> tuple[str, list[dict[str, Any]]]:
        return device_id, _get_device_apps(session, kandji_base_uri, headers, device_id)

    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as executor:
        results = dict(executor.map(fetch, device_ids))

    logger.debug(
        "Kandji apps: %d installs across %d devices",
        sum(len(v) for v in results.values()),
        len(results),
    )
    return results


@timeit
def transform(
    apps_by_device: dict[str, list[dict[str, Any]]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    :return: (apps, installs)
        apps: one row per distinct bundle_id, for KandjiApp
        installs: one row per (device, bundle_id, version), for
            KandjiAppVersion plus its HAS_INSTALLED edge
    """
    apps: dict[str, dict[str, Any]] = {}
    installs: dict[tuple[str, str], dict[str, Any]] = {}
    for device_id, device_apps in apps_by_device.items():
        for app in device_apps:
            bundle_id = app.get("bundle_id")
            if not bundle_id:
                continue
            name = app.get("app_name")
            version = app.get("version") or ""
            apps.setdefault(bundle_id, {"id": bundle_id, "bundle_id": bundle_id})
            if name and not apps[bundle_id].get("name"):
                apps[bundle_id]["name"] = name
            version_id = f"{bundle_id}:{version}"
            # A bundle can appear twice on one device (e.g. two copies at
            # different paths); keep one edge per device+version.
            installs.setdefault(
                (device_id, version_id),
                {
                    "id": version_id,
                    "bundle_id": bundle_id,
                    "name": name,
                    "version": version,
                    "device_id": device_id,
                    "path": app.get("path"),
                    "source": app.get("source"),
                    "signature": app.get("signature"),
                    "modification_date": app.get("modification_date"),
                    "creation_date": app.get("creation_date"),
                },
            )
    return list(apps.values()), list(installs.values())


@timeit
def load_apps(
    neo4j_session: neo4j.Session,
    common_job_parameters: dict[str, Any],
    apps: list[dict[str, Any]],
    installs: list[dict[str, Any]],
) -> None:
    tenant_id = common_job_parameters["TENANT_ID"]
    update_tag = common_job_parameters["UPDATE_TAG"]
    load(
        neo4j_session,
        KandjiAppSchema(),
        apps,
        lastupdated=update_tag,
        TENANT_ID=tenant_id,
    )
    load(
        neo4j_session,
        KandjiAppVersionSchema(),
        installs,
        lastupdated=update_tag,
        TENANT_ID=tenant_id,
    )


def cleanup(
    neo4j_session: neo4j.Session,
    common_job_parameters: dict[str, Any],
) -> None:
    GraphJob.from_node_schema(KandjiAppVersionSchema(), common_job_parameters).run(
        neo4j_session,
    )
    GraphJob.from_node_schema(KandjiAppSchema(), common_job_parameters).run(
        neo4j_session,
    )


@timeit
def sync(
    neo4j_session: neo4j.Session,
    kandji_base_uri: str,
    kandji_token: str,
    device_ids: list[str],
    common_job_parameters: dict[str, Any],
) -> None:
    apps_by_device = get(kandji_base_uri, kandji_token, device_ids)
    apps, installs = transform(apps_by_device)
    load_apps(neo4j_session, common_job_parameters, apps, installs)
    cleanup(neo4j_session, common_job_parameters)
