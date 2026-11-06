import logging
import threading
from concurrent.futures import as_completed
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import neo4j
from googleapiclient.discovery import Resource
from googleapiclient.errors import HttpError

from cartography.client.core.tx import load
from cartography.graph.job import GraphJob
from cartography.intel.gcp.clients import _authorized_http_with_timeout
from cartography.intel.gcp.util import classify_gcp_http_error
from cartography.intel.gcp.util import gcp_api_execute_with_retry
from cartography.intel.gcp.util import summarize_gcp_http_error
from cartography.models.gcp.kms.cryptokey import GCPCryptoKeySchema
from cartography.models.gcp.kms.keyring import GCPKeyRingSchema
from cartography.util import timeit

logger = logging.getLogger(__name__)

# Concurrent keyRings.list calls per project (one per KMS location).
KMS_LOCATION_WORKERS = 16


@timeit
def get_kms_locations(client: Resource, project_id: str) -> list[dict] | None:
    """
    Retrieve KMS locations for a given project.

    :param client: The KMS resource object created by googleapiclient.discovery.build().
    :param project_id: The GCP Project ID to retrieve locations from.
    :return: A list of dictionaries representing KMS locations, or None if API is disabled.
    """
    parent = f"projects/{project_id}"
    locations = []
    try:
        request = client.projects().locations().list(name=parent)
        while request is not None:
            response = gcp_api_execute_with_retry(request)
            locations.extend(response.get("locations", []))
            request = (
                client.projects()
                .locations()
                .list_next(
                    previous_request=request,
                    previous_response=response,
                )
            )
        return locations
    except HttpError as e:
        if classify_gcp_http_error(e) == "api_disabled":
            logger.warning(
                "Could not retrieve KMS locations on project %s due to permissions "
                "issues or API not enabled. Skipping sync to preserve existing data. %s",
                project_id,
                summarize_gcp_http_error(e),
            )
            return None
        raise


@timeit
def get_key_rings(
    client: Resource,
    project_id: str,
    locations: list[dict],
) -> list[dict] | None:
    """
    Retrieve KMS Key Rings for a given project across all locations.

    :param client: The KMS resource object created by googleapiclient.discovery.build().
    :param project_id: The GCP Project ID to retrieve key rings from.
    :param locations: A list of location dictionaries.
    :return: A list of dictionaries representing KMS Key Rings, or None if sync
             should be skipped to preserve existing data.
    """
    location_ids = [loc["locationId"] for loc in locations if loc.get("locationId")]
    # KMS has no wildcard location for keyRings.list (locations/- is rejected),
    # so every location (~70) is a separate call at ~0.6s each, ~45s per
    # KMS-enabled project when run serially. Run them concurrently.
    #
    # googleapiclient's httplib2 transport is not thread-safe, so each worker
    # thread gets its own AuthorizedHttp (same credentials and timeout as the
    # client's); the Resource object itself is only used to build requests.
    credentials = getattr(getattr(client, "_http", None), "credentials", None)
    thread_local = threading.local()

    def _http() -> Any:
        if credentials is None:
            return None  # tests / non-standard clients: use the request's own http
        if not hasattr(thread_local, "http"):
            thread_local.http = _authorized_http_with_timeout(credentials)
        return thread_local.http

    def _list_location(location_id: str) -> list[dict]:
        parent = f"projects/{project_id}/locations/{location_id}"
        location_rings: list[dict] = []
        request = client.projects().locations().keyRings().list(parent=parent)
        while request is not None:
            http = _http()
            if http is not None:
                request.http = http
            response = gcp_api_execute_with_retry(request)
            location_rings.extend(response.get("keyRings", []))
            request = (
                client.projects()
                .locations()
                .keyRings()
                .list_next(
                    previous_request=request,
                    previous_response=response,
                )
            )
        return location_rings

    rings_by_location: dict[str, list[dict]] = {}
    try:
        workers = min(KMS_LOCATION_WORKERS, len(location_ids)) or 1
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(_list_location, location_id): location_id
                for location_id in location_ids
            }
            for future in as_completed(futures):
                rings_by_location[futures[future]] = future.result()
        # Preserve the original per-location order in the result.
        return [ring for lid in location_ids for ring in rings_by_location[lid]]
    except HttpError as e:
        category = classify_gcp_http_error(e)
        if category == "billing_disabled":
            logger.warning(
                "Billing is disabled for project %s while listing KMS key rings. "
                "Skipping KMS sync to preserve existing data. %s",
                project_id,
                summarize_gcp_http_error(e),
            )
            return None
        if category == "api_disabled":
            logger.warning(
                "Could not retrieve KMS key rings on project %s due to permissions "
                "issues or API not enabled. Skipping sync to preserve existing data. %s",
                project_id,
                summarize_gcp_http_error(e),
            )
            return None
        raise


@timeit
def get_crypto_keys(client: Resource, keyring_name: str) -> list[dict]:
    """
    Retrieve Crypto Keys for a given Key Ring.

    :param client: The KMS resource object created by googleapiclient.discovery.build().
    :param keyring_name: The full resource name of the Key Ring.
    :return: A list of dictionaries representing Crypto Keys.
    """
    request = (
        client.projects().locations().keyRings().cryptoKeys().list(parent=keyring_name)
    )

    keys = []
    while request is not None:
        response = gcp_api_execute_with_retry(request)
        keys.extend(response.get("cryptoKeys", []))
        request = (
            client.projects()
            .locations()
            .keyRings()
            .cryptoKeys()
            .list_next(
                previous_request=request,
                previous_response=response,
            )
        )
    return keys


def transform_key_rings(key_rings: list[dict], project_id: str) -> list[dict]:
    transformed = []
    for ring in key_rings:
        ring_id = ring["name"]  # Required field - fail fast if missing
        location = ring_id.split("/")[3]
        transformed.append(
            {
                "id": ring_id,
                "name": ring_id.split("/")[-1],
                "location": location,
                "project_id": project_id,
            },
        )
    return transformed


def transform_crypto_keys(crypto_keys: list[dict], keyring_id: str) -> list[dict]:
    transformed = []
    for key in crypto_keys:
        key_id = key["name"]  # Required field - fail fast if missing
        transformed.append(
            {
                "id": key_id,
                "name": key_id.split("/")[-1],
                "rotation_period": key.get("rotationPeriod"),
                "purpose": key.get("purpose"),
                "state": key.get("primary", {}).get("state"),
                "key_ring_id": keyring_id,
                "project_id": keyring_id.split("/")[1],
            },
        )
    return transformed


@timeit
def load_key_rings(
    neo4j_session: neo4j.Session,
    data: list[dict],
    project_id: str,
    update_tag: int,
) -> None:
    load(
        neo4j_session,
        GCPKeyRingSchema(),
        data,
        lastupdated=update_tag,
        PROJECT_ID=project_id,
    )


@timeit
def load_crypto_keys(
    neo4j_session: neo4j.Session,
    data: list[dict],
    project_id: str,
    update_tag: int,
) -> None:
    load(
        neo4j_session,
        GCPCryptoKeySchema(),
        data,
        lastupdated=update_tag,
        PROJECT_ID=project_id,
    )


@timeit
def cleanup_kms(neo4j_session: neo4j.Session, common_job_parameters: dict) -> None:
    GraphJob.from_node_schema(GCPCryptoKeySchema(), common_job_parameters).run(
        neo4j_session,
    )
    GraphJob.from_node_schema(GCPKeyRingSchema(), common_job_parameters).run(
        neo4j_session,
    )


@timeit
def sync(
    neo4j_session: neo4j.Session,
    kms_client: Resource,
    project_id: str,
    gcp_update_tag: int,
    common_job_parameters: dict[str, Any],
) -> None:
    """
    Sync GCP KMS Key Rings and Crypto Keys for a given project.

    :param neo4j_session: The Neo4j session object.
    :param kms_client: The KMS resource object created by googleapiclient.discovery.build().
    :param project_id: The GCP Project ID to sync.
    :param gcp_update_tag: The update tag for this sync run.
    :param common_job_parameters: Common parameters for cleanup jobs.
    """
    logger.info("Syncing GCP KMS for project %s.", project_id)

    locations = get_kms_locations(kms_client, project_id)
    if locations is None:
        return
    if not locations:
        logger.info("No KMS locations found for project %s.", project_id)

    key_rings_raw = get_key_rings(kms_client, project_id, locations)
    if key_rings_raw is None:
        return
    if not key_rings_raw:
        logger.info("No KMS KeyRings found for project %s.", project_id)
    else:
        key_rings = transform_key_rings(key_rings_raw, project_id)
        load_key_rings(neo4j_session, key_rings, project_id, gcp_update_tag)

        for ring in key_rings_raw:
            keyring_id = ring["name"]
            crypto_keys_raw = get_crypto_keys(kms_client, keyring_id)
            if crypto_keys_raw:
                crypto_keys = transform_crypto_keys(crypto_keys_raw, keyring_id)
                load_crypto_keys(neo4j_session, crypto_keys, project_id, gcp_update_tag)

    cleanup_job_params = common_job_parameters.copy()
    cleanup_job_params["PROJECT_ID"] = project_id
    cleanup_kms(neo4j_session, cleanup_job_params)
