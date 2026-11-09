import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from enum import Enum
from typing import Any

import neo4j
from googleapiclient.discovery import Resource
from googleapiclient.errors import HttpError

from cartography.client.core.tx import load
from cartography.client.core.tx import run_write_query
from cartography.graph.job import GraphJob
from cartography.intel.gcp.clients import _authorized_http_with_timeout
from cartography.intel.gcp.util import gcp_api_execute_with_retry
from cartography.intel.gcp.util import is_gcp_http_error_category
from cartography.models.gcp.bigquery.table import GCPBigQueryTableSchema
from cartography.util import timeit

logger = logging.getLogger(__name__)

# Concurrent tables.get calls per dataset.
TABLE_DETAIL_WORKERS = 16

# Datasets with more tables than this are not inventoried table by table; only
# their external tables are loaded. See TableSyncMode.
TABLE_SYNC_CAP = 500


class TableSyncMode(str, Enum):
    """
    Recorded on each GCPBigQueryDataset as table_sync_mode, so a dataset whose
    tables were not fully loaded is distinguishable from a small one.
    """

    # Every table loaded, with tables.get detail.
    FULL = "full"
    # Over TABLE_SYNC_CAP: only EXTERNAL tables loaded (with detail).
    EXTERNAL_ONLY = "external_only"
    # Hidden dataset: every table loaded from tables.list only, no detail.
    NO_DETAIL = "no_detail"
    # tables.list failed (e.g. access denied): no tables loaded.
    FAILED = "failed"


def _is_hidden_dataset(dataset_id: str) -> bool:
    """
    Datasets whose ID starts with "_" are hidden, e.g. the anonymous datasets
    holding cached query results ("_<hash>", tables "anon..."). They can hold
    thousands of short-lived tables whose tables.get is typically denied, so we
    inventory them from tables.list only and skip the per-table detail calls.
    """
    return dataset_id.startswith("_")


def _normalize_connection_id(connection_id: str | None) -> str | None:
    """
    Normalize a BigQuery connection ID to the full resource name format.

    The API may return connection IDs in either short form
    (``project_number.location.connection_name``) or full resource name form
    (``projects/…/locations/…/connections/…``).  This function ensures we
    always store the full resource name so that relationship matching works.
    """
    if connection_id is None:
        return None
    if connection_id.startswith("projects/"):
        return connection_id
    parts = connection_id.split(".")
    if len(parts) == 3:
        return f"projects/{parts[0]}/locations/{parts[1]}/connections/{parts[2]}"
    return connection_id


@timeit
def get_bigquery_tables(
    client: Resource,
    project_id: str,
    dataset_id: str,
) -> list[dict] | None:
    """
    Gets BigQuery tables for a dataset.

    Returns:
        list[dict]: List of BigQuery tables (empty list if dataset has no tables)
        None: If the BigQuery API is not enabled or access is denied

    Raises:
        HttpError: For errors other than API disabled or permission denied
    """
    try:
        tables: list[dict] = []
        request = client.tables().list(projectId=project_id, datasetId=dataset_id)
        while request is not None:
            response = gcp_api_execute_with_retry(request)
            tables.extend(response.get("tables", []))
            request = client.tables().list_next(
                previous_request=request,
                previous_response=response,
            )
        return tables
    except HttpError as e:
        if is_gcp_http_error_category(
            e,
            ("api_disabled", "billing_disabled", "forbidden", "not_found"),
            include_transient_403=True,
        ):
            logger.warning(
                "Could not retrieve BigQuery tables for dataset %s:%s - %s. Skipping.",
                project_id,
                dataset_id,
                e,
            )
            return None
        raise


@timeit
def get_bigquery_table_detail(
    client: Resource,
    project_id: str,
    dataset_id: str,
    table_id: str,
    http: Any = None,
) -> dict | None:
    """
    Gets full details for a single BigQuery table via tables.get.

    tables.list does not return numBytes, numRows, numLongTermBytes, description,
    friendlyName, or externalDataConfiguration. We call tables.get per table to
    retrieve these fields.

    :param http: Optional transport to execute the request on, for callers
        running this from worker threads (httplib2 is not thread-safe).

    Returns:
        dict: The full table resource
        None: If the table could not be retrieved

    Raises:
        HttpError: For errors other than API disabled or permission denied
    """
    try:
        request = client.tables().get(
            projectId=project_id,
            datasetId=dataset_id,
            tableId=table_id,
        )
        if http is not None:
            request.http = http
        return gcp_api_execute_with_retry(request)
    except HttpError as e:
        if is_gcp_http_error_category(
            e,
            ("api_disabled", "billing_disabled", "forbidden", "invalid", "not_found"),
            include_transient_403=True,
        ):
            logger.warning(
                "Could not retrieve BigQuery table detail for %s:%s.%s - %s. Skipping.",
                project_id,
                dataset_id,
                table_id,
                e,
            )
            return None
        raise


def transform_tables(
    tables_data: list[dict],
    project_id: str,
    dataset_full_id: str,
) -> list[dict]:
    transformed: list[dict] = []
    for table in tables_data:
        ref = table["tableReference"]
        table_id = ref["tableId"]
        ext_config = table.get("externalDataConfiguration", {}) or {}
        connection_id = _normalize_connection_id(ext_config.get("connectionId"))
        transformed.append(
            {
                "id": f"{dataset_full_id}.{table_id}",
                "table_id": table_id,
                "dataset_id": dataset_full_id,
                "type": table.get("type"),
                "creation_time": table.get("creationTime"),
                "expiration_time": table.get("expirationTime"),
                "num_bytes": table.get("numBytes"),
                "num_long_term_bytes": table.get("numLongTermBytes"),
                "num_rows": table.get("numRows"),
                "description": table.get("description"),
                "friendly_name": table.get("friendlyName"),
                "connection_id": connection_id,
                "kms_key_name": table.get("encryptionConfiguration", {}).get(
                    "kmsKeyName"
                ),
            },
        )
    return transformed


@timeit
def load_bigquery_tables(
    neo4j_session: neo4j.Session,
    data: list[dict],
    project_id: str,
    update_tag: int,
) -> None:
    load(
        neo4j_session,
        GCPBigQueryTableSchema(),
        data,
        lastupdated=update_tag,
        PROJECT_ID=project_id,
    )


def _set_dataset_table_sync_status(
    neo4j_session: neo4j.Session,
    dataset_full_id: str,
    mode: TableSyncMode,
    table_count: int | None,
    tables_synced: int,
) -> None:
    run_write_query(
        neo4j_session,
        """
        MATCH (d:GCPBigQueryDataset {id: $DATASET_ID})
        SET d.table_sync_mode = $MODE,
            d.table_count = $TABLE_COUNT,
            d.tables_synced = $TABLES_SYNCED,
            d.table_sync_cap = $CAP
        """,
        DATASET_ID=dataset_full_id,
        MODE=mode.value,
        TABLE_COUNT=table_count,
        TABLES_SYNCED=tables_synced,
        CAP=TABLE_SYNC_CAP,
    )


@timeit
def cleanup_bigquery_tables(
    neo4j_session: neo4j.Session,
    common_job_parameters: dict,
) -> None:
    GraphJob.from_node_schema(GCPBigQueryTableSchema(), common_job_parameters).run(
        neo4j_session,
    )


@timeit
def sync_bigquery_tables(
    neo4j_session: neo4j.Session,
    client: Resource,
    datasets: list[dict],
    project_id: str,
    update_tag: int,
    common_job_parameters: dict,
) -> None:
    logger.info("Syncing BigQuery tables for project %s.", project_id)

    # tables.get is one call per table, so run them concurrently. httplib2 is
    # not thread-safe, so each worker thread gets its own AuthorizedHttp (same
    # credentials and timeout as the client's).
    credentials = getattr(getattr(client, "_http", None), "credentials", None)
    thread_local = threading.local()

    def _http() -> Any:
        if credentials is None:
            return None  # tests / non-standard clients: use the request's own http
        if not hasattr(thread_local, "http"):
            thread_local.http = _authorized_http_with_timeout(credentials)
        return thread_local.http

    # Each dataset is fetched, loaded, and released before the next one, so
    # memory is bounded by one dataset rather than the whole project.
    with ThreadPoolExecutor(max_workers=TABLE_DETAIL_WORKERS) as executor:
        for dataset in datasets:
            dataset_id = dataset["datasetReference"]["datasetId"]
            dataset_full_id = f"{project_id}:{dataset_id}"

            tables_raw = get_bigquery_tables(client, project_id, dataset_id)
            if tables_raw is None:
                _set_dataset_table_sync_status(
                    neo4j_session,
                    dataset_full_id,
                    TableSyncMode.FAILED,
                    table_count=None,
                    tables_synced=0,
                )
                continue

            table_count = len(tables_raw)
            if table_count > TABLE_SYNC_CAP:
                # Too many tables to inventory (typically auto-generated, e.g.
                # date-sharded exports). Keep only external tables, which point
                # at data outside BigQuery.
                mode = TableSyncMode.EXTERNAL_ONLY
                tables_raw = [t for t in tables_raw if t.get("type") == "EXTERNAL"]
            elif _is_hidden_dataset(dataset_id):
                mode = TableSyncMode.NO_DETAIL
            else:
                mode = TableSyncMode.FULL

            if mode != TableSyncMode.NO_DETAIL and tables_raw:
                details = executor.map(
                    lambda tid, ds=dataset_id: get_bigquery_table_detail(
                        client, project_id, ds, tid, http=_http()
                    ),
                    [t["tableReference"]["tableId"] for t in tables_raw],
                )
                for table, detail in zip(tables_raw, details):
                    if detail is not None:
                        table.update(detail)

            if tables_raw:
                load_bigquery_tables(
                    neo4j_session,
                    transform_tables(tables_raw, project_id, dataset_full_id),
                    project_id,
                    update_tag,
                )
            _set_dataset_table_sync_status(
                neo4j_session,
                dataset_full_id,
                mode,
                table_count=table_count,
                tables_synced=len(tables_raw),
            )
            logger.info(
                "BigQuery dataset %s: %d tables, %d synced (mode=%s).",
                dataset_full_id,
                table_count,
                len(tables_raw),
                mode.value,
            )

    cleanup_job_params = common_job_parameters.copy()
    cleanup_job_params["PROJECT_ID"] = project_id
    cleanup_bigquery_tables(neo4j_session, cleanup_job_params)
