# Google Compute Engine Instance Groups
# https://cloud.google.com/compute/docs/reference/rest/v1/instanceGroups
# https://cloud.google.com/compute/docs/reference/rest/v1/regionInstanceGroups
from __future__ import annotations

import logging
from typing import Any

import neo4j
from googleapiclient.discovery import Resource
from googleapiclient.errors import HttpError

from cartography.client.core.tx import load
from cartography.graph.job import GraphJob
from cartography.intel.gcp.util import classify_gcp_http_error
from cartography.intel.gcp.util import gcp_api_execute_with_retry
from cartography.intel.gcp.util import parse_compute_full_uri_to_partial_uri
from cartography.intel.gcp.util import summarize_gcp_http_error
from cartography.models.gcp.compute.instance_group import GCPInstanceGroupSchema
from cartography.util import timeit

logger = logging.getLogger(__name__)


def _get_instance_group_members(
    project_id: str,
    instance_group_name: str,
    zone: str | None,
    region: str | None,
    compute: Resource,
) -> list[dict]:
    """
    Return list of member instances for a given instance group.
    :param project_id: The project ID
    :param instance_group_name: The name of the instance group
    :param zone: The zone (for zonal instance groups), or None
    :param region: The region (for regional instance groups), or None
    :param compute: The compute resource object
    :return: List of member dicts with 'instance' (URL) and 'status' fields
    """
    if zone:
        req = compute.instanceGroups().listInstances(
            project=project_id,
            zone=zone,
            instanceGroup=instance_group_name,
            body={"instanceState": "ALL"},
        )
        list_next_fn = compute.instanceGroups().listInstances_next
    elif region:
        req = compute.regionInstanceGroups().listInstances(
            project=project_id,
            region=region,
            instanceGroup=instance_group_name,
            body={"instanceState": "ALL"},
        )
        list_next_fn = compute.regionInstanceGroups().listInstances_next
    else:
        return []

    members: list[dict] = []
    while req is not None:
        try:
            res = gcp_api_execute_with_retry(req)
        except HttpError as e:
            category = classify_gcp_http_error(e)
            if category == "transient":
                logger.warning(
                    "Transient error listing members for instance group %s: %s; skipping.",
                    instance_group_name,
                    summarize_gcp_http_error(e),
                )
                return []
            if category == "forbidden":
                logger.warning(
                    "Missing permissions listing members for instance group %s: %s; skipping members.",
                    instance_group_name,
                    summarize_gcp_http_error(e),
                )
                return []
            if category in ("api_disabled", "billing_disabled"):
                logger.info(
                    "Compute API unavailable for listing members of instance group %s: %s; skipping members.",
                    instance_group_name,
                    summarize_gcp_http_error(e),
                )
                return []
            raise
        members.extend(res.get("items", []))
        req = list_next_fn(previous_request=req, previous_response=res)
    return members


def _get_aggregated_instance_groups(
    project_id: str,
    compute: Resource,
) -> tuple[dict[str, list[dict]], dict[str, list[dict]]]:
    """
    Fetch every zonal and regional instance group in a project with one
    instanceGroups.aggregatedList chain, instead of one list call per zone plus
    one per region (~175 calls, ~90s per Compute-enabled project even when it
    has no instance groups). Scopes come back keyed "zones/{zone}" or
    "regions/{region}".

    :return: (groups by zone name, groups by region name)
    """
    by_zone: dict[str, list[dict]] = {}
    by_region: dict[str, list[dict]] = {}
    req = compute.instanceGroups().aggregatedList(
        project=project_id,
        returnPartialSuccess=True,
    )
    while req is not None:
        try:
            res = gcp_api_execute_with_retry(req)
        except HttpError as e:
            if classify_gcp_http_error(e) == "transient":
                logger.warning(
                    "Transient error listing instance groups for project %s: %s; keeping groups fetched so far.",
                    project_id,
                    summarize_gcp_http_error(e),
                )
                break
            raise
        for scope, scoped_list in res.get("items", {}).items():
            warning_code = (scoped_list.get("warning") or {}).get("code")
            if warning_code and warning_code != "NO_RESULTS_ON_PAGE":
                logger.warning(
                    "GCP: instanceGroups.aggregatedList for project %s reported %s for scope %s.",
                    project_id,
                    warning_code,
                    scope,
                )
            groups = scoped_list.get("instanceGroups")
            if not groups:
                continue
            kind, _, name = scope.partition("/")
            if kind == "zones":
                by_zone.setdefault(name, []).extend(groups)
            elif kind == "regions":
                by_region.setdefault(name, []).extend(groups)
        req = compute.instanceGroups().aggregatedList_next(
            previous_request=req,
            previous_response=res,
        )
    return by_zone, by_region


def _to_responses(
    project_id: str,
    groups_by_location: dict[str, list[dict]],
    location_kind: str,
    compute: Resource,
) -> list[Resource]:
    """
    Enrich each group with '_members' and regroup into the per-location
    {id, items} response objects transform_gcp_instance_groups() expects.
    """
    response_objects: list[Resource] = []
    for location, items in groups_by_location.items():
        for ig in items:
            ig["_members"] = _get_instance_group_members(
                project_id,
                ig["name"],
                location if location_kind == "zones" else None,
                location if location_kind == "regions" else None,
                compute,
            )
        response_objects.append(
            {
                "id": f"projects/{project_id}/{location_kind}/{location}/instanceGroups",
                "items": items,
            }
        )
    return response_objects


@timeit
def get_gcp_instance_groups(
    project_id: str,
    compute: Resource,
) -> tuple[list[Resource], list[Resource]]:
    """
    Return (zonal, regional) instance group response objects for a project.
    Each instance group item is enriched with a '_members' field containing its member instances.
    :return: Two lists of response objects of the form {id: str, items: [...]}
    """
    by_zone, by_region = _get_aggregated_instance_groups(project_id, compute)
    return (
        _to_responses(project_id, by_zone, "zones", compute),
        _to_responses(project_id, by_region, "regions", compute),
    )


@timeit
def transform_gcp_instance_groups(
    response_objects: list[Resource],
    project_id: str,
) -> list[dict]:
    """
    Transform instance group response objects for Neo4j ingestion.
    Expects each instance group item to have a '_members' field populated by the get functions.
    :param response_objects: List of response objects from the get functions
    :param project_id: The project ID
    :return: List of transformed instance group dicts ready for loading
    """
    instance_group_list: list[dict] = []

    for response in response_objects:
        prefix = response["id"]

        for ig in response.get("items", []):
            instance_group: dict[str, Any] = {}

            partial_uri = f"{prefix}/{ig['name']}"
            instance_group["partial_uri"] = partial_uri
            instance_group["project_id"] = project_id
            instance_group["name"] = ig.get("name")
            instance_group["self_link"] = ig.get("selfLink")
            instance_group["description"] = ig.get("description")
            instance_group["size"] = ig.get("size")
            instance_group["creation_timestamp"] = ig.get("creationTimestamp")

            zone_url = ig.get("zone")
            instance_group["zone"] = zone_url.split("/")[-1] if zone_url else None

            region_url = ig.get("region")
            instance_group["region"] = region_url.split("/")[-1] if region_url else None

            network = ig.get("network")
            instance_group["network_partial_uri"] = (
                parse_compute_full_uri_to_partial_uri(network)
            )

            subnetwork = ig.get("subnetwork")
            instance_group["subnetwork_partial_uri"] = (
                parse_compute_full_uri_to_partial_uri(subnetwork)
            )

            # Member instances from _members (populated by get functions)
            instance_group["member_instance_partial_uris"] = [
                parse_compute_full_uri_to_partial_uri(member_url)
                for m in ig.get("_members", [])
                if (member_url := m.get("instance"))
            ]

            instance_group_list.append(instance_group)
    return instance_group_list


@timeit
def load_gcp_instance_groups(
    neo4j_session: neo4j.Session,
    instance_groups: list[dict],
    gcp_update_tag: int,
    project_id: str,
) -> None:
    """
    Ingest GCP instance group data to Neo4j.
    """
    load(
        neo4j_session,
        GCPInstanceGroupSchema(),
        instance_groups,
        lastupdated=gcp_update_tag,
        PROJECT_ID=project_id,
    )


@timeit
def cleanup_gcp_instance_groups(
    neo4j_session: neo4j.Session,
    common_job_parameters: dict,
) -> None:
    """
    Delete out-of-date GCP instance groups and relationships.
    """
    GraphJob.from_node_schema(GCPInstanceGroupSchema(), common_job_parameters).run(
        neo4j_session,
    )


@timeit
def sync_gcp_instance_groups(
    neo4j_session: neo4j.Session,
    compute: Resource,
    project_id: str,
    zones: list[dict],
    regions: list[str],
    gcp_update_tag: int,
    common_job_parameters: dict,
) -> None:
    """
    Sync GCP zonal and regional instance groups, ingest to Neo4j, and clean up old data.
    """
    logger.info("Syncing GCP instance groups for project %s", project_id)

    # One aggregated call covers zonal and regional groups. `zones` and
    # `regions` are no longer needed for the fetch; they stay in the signature
    # so callers are unchanged. The only caller (compute.sync) already returns
    # early when the Compute API is disabled.
    zonal_responses, regional_responses = get_gcp_instance_groups(project_id, compute)

    # Zonal instance groups
    instance_groups = transform_gcp_instance_groups(zonal_responses, project_id)
    load_gcp_instance_groups(neo4j_session, instance_groups, gcp_update_tag, project_id)

    # Regional instance groups
    instance_groups = transform_gcp_instance_groups(regional_responses, project_id)
    load_gcp_instance_groups(neo4j_session, instance_groups, gcp_update_tag, project_id)

    # Cleanup after both zonal and regional are loaded
    cleanup_gcp_instance_groups(neo4j_session, common_job_parameters)
