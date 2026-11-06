from unittest.mock import MagicMock

from cartography.intel.gcp.instancegroup import get_gcp_instance_groups


def _request(response):
    request = MagicMock()
    request.execute.return_value = response
    return request


def test_get_gcp_instance_groups_splits_zonal_and_regional_from_one_call():
    compute = MagicMock()
    igs = compute.instanceGroups.return_value
    igs.aggregatedList.return_value = _request(
        {
            "items": {
                "zones/us-central1-a": {"instanceGroups": [{"name": "zig"}]},
                "zones/us-east1-b": {"warning": {"code": "NO_RESULTS_ON_PAGE"}},
                "regions/us-east1": {"instanceGroups": [{"name": "rig"}]},
            }
        }
    )
    igs.aggregatedList_next.return_value = None
    igs.listInstances.return_value = _request(
        {
            "items": [
                {
                    "instance": "https://x/compute/v1/projects/p/zones/us-central1-a/instances/vm"
                }
            ]
        }
    )
    igs.listInstances_next.return_value = None
    rigs = compute.regionInstanceGroups.return_value
    rigs.listInstances.return_value = _request({"items": []})
    rigs.listInstances_next.return_value = None

    zonal, regional = get_gcp_instance_groups("p", compute)

    # One aggregated call, no per-zone/per-region list calls.
    igs.aggregatedList.assert_called_once_with(project="p", returnPartialSuccess=True)
    igs.list.assert_not_called()
    rigs.list.assert_not_called()

    assert [r["id"] for r in zonal] == ["projects/p/zones/us-central1-a/instanceGroups"]
    assert [r["id"] for r in regional] == ["projects/p/regions/us-east1/instanceGroups"]
    assert zonal[0]["items"][0]["_members"][0]["instance"].endswith("/instances/vm")
    assert regional[0]["items"][0]["_members"] == []

    # Members are fetched with the right location type.
    igs.listInstances.assert_called_once()
    assert igs.listInstances.call_args.kwargs["zone"] == "us-central1-a"
    rigs.listInstances.assert_called_once()
    assert rigs.listInstances.call_args.kwargs["region"] == "us-east1"
