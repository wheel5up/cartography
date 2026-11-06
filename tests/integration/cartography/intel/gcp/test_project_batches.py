"""
Project batching: running batch 0 and batch 1 of an org with one shared
update_tag must leave each project's resources in place (one batch's
per-project cleanup must not touch the other batch's projects), and a later
full run with a new tag must still clean up stale resources.
"""

import copy
from unittest.mock import MagicMock
from unittest.mock import patch

import cartography.intel.gcp
import cartography.intel.gcp.crm.folders
import cartography.intel.gcp.crm.orgs
import cartography.intel.gcp.crm.projects
import cartography.intel.gcp.storage
import tests.data.gcp.crm
import tests.data.gcp.storage
from cartography.config import Config
from tests.integration import settings
from tests.integration.util import check_nodes

TAG = 222000001
TAG_2 = 222000002
PROJECTS = [
    {**tests.data.gcp.crm.GCP_PROJECTS[0], "projectId": "batch-a", "name": "A"},
    {**tests.data.gcp.crm.GCP_PROJECTS[0], "projectId": "batch-b", "name": "B"},
]


def _buckets_for(project_id: str) -> dict:
    res: dict = copy.deepcopy(dict(tests.data.gcp.storage.STORAGE_RESPONSE))
    for item in res["items"]:
        item["id"] = f"{project_id}-bucket"
        item["name"] = f"{project_id}-bucket"
    return res


def _run(neo4j_session, update_tag, batch_index=None, batch_count=None):
    config = Config(
        neo4j_uri=settings.get("NEO4J_URL"),
        update_tag=update_tag,
        gcp_requested_syncs="storage",
        gcp_project_batch_index=batch_index,
        gcp_project_batch_count=batch_count,
    )
    gcp = cartography.intel.gcp
    with (
        patch.object(
            cartography.intel.gcp.crm.orgs,
            "get_gcp_organizations",
            return_value=tests.data.gcp.crm.GCP_ORGANIZATIONS,
        ),
        patch.object(
            cartography.intel.gcp.crm.folders, "get_gcp_folders", return_value=[]
        ),
        patch.object(
            cartography.intel.gcp.crm.projects,
            "get_gcp_projects",
            return_value=PROJECTS,
        ),
        patch.object(gcp, "build_client", return_value=MagicMock()),
        patch.object(
            gcp,
            "_services_enabled_on_project",
            return_value={"storage.googleapis.com"},
        ),
        patch.object(
            cartography.intel.gcp.storage,
            "get_gcp_buckets",
            side_effect=lambda _client, project_id: _buckets_for(project_id),
        ),
    ):
        gcp.start_gcp_ingestion(neo4j_session, config, credentials=MagicMock())


def test_batches_share_tag_without_deleting_each_other(neo4j_session):
    neo4j_session.run("MATCH (n) DETACH DELETE n")

    _run(neo4j_session, TAG, 0, 2)
    assert check_nodes(neo4j_session, "GCPBucket", ["id"]) == {("batch-a-bucket",)}

    _run(neo4j_session, TAG, 1, 2)
    # Batch 1 must not have removed batch 0's bucket.
    assert check_nodes(neo4j_session, "GCPBucket", ["id"]) == {
        ("batch-a-bucket",),
        ("batch-b-bucket",),
    }
    assert check_nodes(neo4j_session, "GCPProject", ["id"]) == {
        ("batch-a",),
        ("batch-b",),
    }


def test_full_run_with_new_tag_still_cleans_up(neo4j_session):
    neo4j_session.run("MATCH (n) DETACH DELETE n")
    _run(neo4j_session, TAG, 0, 2)
    _run(neo4j_session, TAG, 1, 2)

    # A stale bucket under batch-a from an earlier run.
    neo4j_session.run(
        """
        MATCH (p:GCPProject {id: 'batch-a'})
        MERGE (p)-[r:RESOURCE]->(b:GCPBucket {id: 'stale-bucket'})
        SET b.lastupdated = 1, r.lastupdated = 1
        """
    )
    _run(neo4j_session, TAG_2, 0, 2)
    assert ("stale-bucket",) not in check_nodes(neo4j_session, "GCPBucket", ["id"])


def _seed_org_binding(neo4j_session, binding_id, update_tag):
    org_id = tests.data.gcp.crm.GCP_ORGANIZATIONS[0]["name"]
    neo4j_session.run(
        """
        MATCH (org:GCPOrganization {id: $org_id})
        MERGE (b:GCPPolicyBinding {id: $binding_id})
        SET b.lastupdated = $tag
        MERGE (org)-[r:RESOURCE]->(b)
        SET r.lastupdated = $tag
        """,
        org_id=org_id,
        binding_id=binding_id,
        tag=update_tag,
    )


def _finalize(neo4j_session, update_tag, complete):
    config = Config(neo4j_uri=settings.get("NEO4J_URL"), update_tag=update_tag)
    with patch.object(cartography.intel.gcp, "get_gcp_folders", return_value=[]):
        cartography.intel.gcp.finalize_gcp_ingestion(
            neo4j_session,
            config,
            [tests.data.gcp.crm.GCP_ORGANIZATIONS[0]["name"]],
            complete,
            credentials=MagicMock(),
        )


def _binding_ids(neo4j_session):
    return {i for (i,) in check_nodes(neo4j_session, "GCPPolicyBinding", ["id"])}


def test_finalize_cleans_stale_org_bindings_only_when_complete(neo4j_session):
    neo4j_session.run("MATCH (n) DETACH DELETE n")
    _run(neo4j_session, TAG, 0, 2)
    _run(neo4j_session, TAG, 1, 2)
    # One binding refreshed this run (e.g. by a batch 0 project), one stale.
    _seed_org_binding(neo4j_session, "fresh-org-binding", TAG)
    _seed_org_binding(neo4j_session, "stale-org-binding", TAG - 1)

    # A batch reported incomplete bindings: nothing is deleted.
    _finalize(neo4j_session, TAG, complete=False)
    assert _binding_ids(neo4j_session) == {"fresh-org-binding", "stale-org-binding"}

    # All batches complete: only the stale binding goes.
    _finalize(neo4j_session, TAG, complete=True)
    assert _binding_ids(neo4j_session) == {"fresh-org-binding"}
