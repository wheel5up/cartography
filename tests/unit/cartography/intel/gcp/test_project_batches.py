from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

import cartography.intel.gcp
from cartography.config import Config
from cartography.intel.gcp import GCPProjectResourcesSyncResult
from cartography.intel.gcp import select_project_batch

ORG = "organizations/1"
PROJECTS = [{"projectId": f"p{i:02d}"} for i in (7, 3, 9, 1, 5, 0, 8, 2, 6, 4)]


def test_select_project_batch_is_deterministic_disjoint_and_complete():
    batches = [select_project_batch(PROJECTS, i, 3) for i in range(3)]
    ids = [[p["projectId"] for p in b] for b in batches]

    # Round-robin over projects sorted by id.
    assert ids == [
        ["p00", "p03", "p06", "p09"],
        ["p01", "p04", "p07"],
        ["p02", "p05", "p08"],
    ]
    flat = sorted(pid for b in ids for pid in b)
    assert flat == sorted(p["projectId"] for p in PROJECTS)
    # Input order doesn't matter.
    assert select_project_batch(list(reversed(PROJECTS)), 1, 3) == batches[1]


def test_select_project_batch_without_batching_returns_everything():
    assert select_project_batch(PROJECTS, None, None) is PROJECTS


@pytest.mark.parametrize(
    "index,count",
    [(0, None), (None, 2), (2, 2), (-1, 2), (0, 0)],
)
def test_config_rejects_invalid_batch_settings(index, count):
    with pytest.raises(ValueError):
        Config(
            neo4j_uri="bolt://x",
            gcp_project_batch_index=index,
            gcp_project_batch_count=count,
        )


def _run(batch_index, batch_count, bindings_safe=True):
    config = Config(
        neo4j_uri="bolt://x",
        update_tag=100,
        gcp_project_batch_index=batch_index,
        gcp_project_batch_count=batch_count,
    )
    gcp = cartography.intel.gcp
    with (
        patch.object(gcp, "sync_gcp_organizations", return_value=[{"name": ORG}]),
        patch.object(gcp, "sync_gcp_folders", return_value=[{"name": "folders/9"}]),
        patch.object(gcp, "sync_gcp_projects", return_value=PROJECTS),
        patch.object(gcp, "build_client"),
        patch.object(gcp.iam, "sync_org_iam", return_value=[]),
        patch.object(gcp.iam, "cleanup_org_roles") as cleanup_org_roles,
        patch.object(
            gcp,
            "_sync_project_resources",
            return_value=GCPProjectResourcesSyncResult(bindings_safe),
        ) as sync_resources,
        patch.object(
            gcp.policy_bindings, "cleanup_inherited_policy_bindings"
        ) as cleanup_inherited,
        patch.object(gcp, "GraphJob") as graph_job,
        patch.object(gcp, "run_analysis_job") as run_analysis,
        patch.object(gcp, "run_typed_analysis_job") as run_typed,
    ):
        result = gcp.start_gcp_ingestion(MagicMock(), config, credentials=MagicMock())
    synced = [p["projectId"] for p in sync_resources.call_args.args[1]]
    return {
        "result": result,
        "synced": synced,
        "cleanup_inherited": cleanup_inherited.call_count,
        "cleanup_org_roles": cleanup_org_roles.call_count,
        "crm_cleanup_jobs": graph_job.from_node_schema.call_count,
        "analysis": run_analysis.call_count + run_typed.call_count,
    }


def test_unbatched_run_syncs_all_projects_and_runs_everything():
    r = _run(None, None)
    assert r["result"] is None
    assert sorted(r["synced"]) == sorted(p["projectId"] for p in PROJECTS)
    assert r["cleanup_inherited"] == 1
    assert r["analysis"] > 0


@pytest.mark.parametrize(
    "index,expected",
    [(0, ["p00", "p03", "p06", "p09"]), (2, ["p02", "p05", "p08"])],
)
def test_every_batch_defers_shared_cleanup_and_analysis(index, expected):
    r = _run(index, 3)
    assert r["synced"] == expected
    # Shared cleanup and analysis belong to finalize, even for the last batch.
    assert r["cleanup_inherited"] == 0
    assert r["analysis"] == 0
    # Org roles are refreshed by sync_org_iam every batch, so cleanup is safe.
    assert r["cleanup_org_roles"] == 1
    # Project/folder/org CRM cleanup still runs: the full project list is synced.
    assert r["crm_cleanup_jobs"] == 3
    assert r["result"] == gcp_batch_result([ORG], True)


def test_batch_reports_incomplete_policy_bindings():
    r = _run(1, 3, bindings_safe=False)
    assert r["result"] == gcp_batch_result([ORG], False)


def gcp_batch_result(org_ids, complete):
    return cartography.intel.gcp.GCPBatchResult(
        org_ids=org_ids, policy_bindings_complete=complete
    )


def _finalize(org_ids, complete, requested_syncs=None):
    config = Config(
        neo4j_uri="bolt://x", update_tag=100, gcp_requested_syncs=requested_syncs
    )
    gcp = cartography.intel.gcp
    with (
        patch.object(
            gcp, "get_gcp_folders", return_value=[{"name": "folders/9"}]
        ) as get_folders,
        patch.object(
            gcp.policy_bindings, "cleanup_inherited_policy_bindings"
        ) as cleanup_inherited,
        patch.object(gcp, "run_analysis_job") as run_analysis,
        patch.object(gcp, "run_typed_analysis_job") as run_typed,
    ):
        gcp.finalize_gcp_ingestion(
            MagicMock(), config, org_ids, complete, credentials=MagicMock()
        )
    return get_folders, cleanup_inherited, run_analysis.call_count + run_typed.call_count


def test_finalize_cleans_each_org_with_its_folders_and_runs_analysis():
    get_folders, cleanup, analysis = _finalize([ORG, "organizations/2"], True)
    assert [c.args[0] for c in get_folders.call_args_list] == [ORG, "organizations/2"]
    assert cleanup.call_count == 2
    params, folder_ids = cleanup.call_args_list[0].args[1:]
    assert params["ORG_RESOURCE_NAME"] == ORG
    assert params["UPDATE_TAG"] == 100
    assert folder_ids == ["folders/9"]
    assert analysis > 0


def test_finalize_preserves_bindings_when_a_batch_was_incomplete():
    get_folders, cleanup, analysis = _finalize([ORG], False)
    cleanup.assert_not_called()
    get_folders.assert_not_called()
    assert analysis > 0


def test_finalize_skips_binding_cleanup_when_not_requested():
    _, cleanup, _ = _finalize([ORG], True, requested_syncs="storage")
    cleanup.assert_not_called()
