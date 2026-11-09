from unittest.mock import MagicMock
from unittest.mock import patch

import cartography.intel.gcp.bigquery_table as bigquery_table
from cartography.intel.gcp.bigquery_table import TableSyncMode


def _dataset(dataset_id):
    return {"datasetReference": {"datasetId": dataset_id}}


def _tables(dataset_id, n, external=()):
    return [
        {
            "tableReference": {"datasetId": dataset_id, "tableId": f"t{i}"},
            "type": "EXTERNAL" if i in external else "TABLE",
        }
        for i in range(n)
    ]


def _client():
    client = MagicMock()
    client._http = None
    return client


def _loaded(mock_load):
    return [t for c in mock_load.call_args_list for t in c.args[1]]


def _status(mock_status):
    """{dataset_id: (mode, table_count, tables_synced)}"""
    return {
        c.args[1]: (c.args[2], c.kwargs["table_count"], c.kwargs["tables_synced"])
        for c in mock_status.call_args_list
    }


@patch.object(bigquery_table, "_set_dataset_table_sync_status")
@patch.object(bigquery_table, "cleanup_bigquery_tables")
@patch.object(bigquery_table, "load_bigquery_tables")
@patch.object(bigquery_table, "get_bigquery_table_detail")
@patch.object(bigquery_table, "get_bigquery_tables")
def test_sync_tables_fetches_details_concurrently_in_order(
    mock_get_tables, mock_get_detail, mock_load, mock_cleanup, mock_status
):
    mock_get_tables.side_effect = lambda client, project_id, dataset_id: _tables(
        dataset_id, 50
    )
    mock_get_detail.side_effect = (
        lambda client, project_id, dataset_id, table_id, http=None: {
            "numRows": f"{dataset_id}.{table_id}"
        }
    )

    bigquery_table.sync_bigquery_tables(
        MagicMock(), _client(), [_dataset("ds")], "p", 1, {"UPDATE_TAG": 1}
    )

    assert mock_get_detail.call_count == 50
    loaded = _loaded(mock_load)
    assert [t["table_id"] for t in loaded] == [f"t{i}" for i in range(50)]
    assert [t["num_rows"] for t in loaded] == [f"ds.t{i}" for i in range(50)]
    assert _status(mock_status) == {"p:ds": (TableSyncMode.FULL, 50, 50)}


@patch.object(bigquery_table, "_set_dataset_table_sync_status")
@patch.object(bigquery_table, "cleanup_bigquery_tables")
@patch.object(bigquery_table, "load_bigquery_tables")
@patch.object(bigquery_table, "get_bigquery_table_detail")
@patch.object(bigquery_table, "get_bigquery_tables")
def test_sync_tables_skips_details_for_hidden_datasets(
    mock_get_tables, mock_get_detail, mock_load, mock_cleanup, mock_status
):
    mock_get_tables.side_effect = lambda client, project_id, dataset_id: _tables(
        dataset_id, 3
    )
    mock_get_detail.return_value = {"numRows": "7"}

    bigquery_table.sync_bigquery_tables(
        MagicMock(),
        _client(),
        [_dataset("_0123abcd"), _dataset("visible")],
        "p",
        1,
        {"UPDATE_TAG": 1},
    )

    called_datasets = {c.args[2] for c in mock_get_detail.call_args_list}
    assert called_datasets == {"visible"}
    loaded = _loaded(mock_load)
    # Hidden dataset tables are still inventoried, just without detail fields.
    assert {(t["dataset_id"], t["num_rows"]) for t in loaded} == {
        ("p:_0123abcd", None),
        ("p:visible", "7"),
    }
    assert len(loaded) == 6
    assert _status(mock_status) == {
        "p:_0123abcd": (TableSyncMode.NO_DETAIL, 3, 3),
        "p:visible": (TableSyncMode.FULL, 3, 3),
    }


@patch.object(bigquery_table, "TABLE_SYNC_CAP", 10)
@patch.object(bigquery_table, "_set_dataset_table_sync_status")
@patch.object(bigquery_table, "cleanup_bigquery_tables")
@patch.object(bigquery_table, "load_bigquery_tables")
@patch.object(bigquery_table, "get_bigquery_table_detail")
@patch.object(bigquery_table, "get_bigquery_tables")
def test_sync_tables_over_cap_loads_only_external_tables(
    mock_get_tables, mock_get_detail, mock_load, mock_cleanup, mock_status
):
    by_dataset = {
        "big": _tables("big", 11, external=(3, 8)),
        "big_no_ext": _tables("big_no_ext", 11),
        "small": _tables("small", 10),
    }
    mock_get_tables.side_effect = lambda client, project_id, dataset_id: by_dataset[
        dataset_id
    ]
    mock_get_detail.return_value = {"numRows": "1"}

    bigquery_table.sync_bigquery_tables(
        MagicMock(),
        _client(),
        [_dataset("big"), _dataset("big_no_ext"), _dataset("small")],
        "p",
        1,
        {"UPDATE_TAG": 1},
    )

    detail_calls = [(c.args[2], c.args[3]) for c in mock_get_detail.call_args_list]
    assert sorted(detail_calls) == sorted(
        [("big", "t3"), ("big", "t8")] + [("small", f"t{i}") for i in range(10)]
    )
    loaded_ids = {t["id"] for t in _loaded(mock_load)}
    assert loaded_ids == {"p:big.t3", "p:big.t8"} | {f"p:small.t{i}" for i in range(10)}
    assert _status(mock_status) == {
        "p:big": (TableSyncMode.EXTERNAL_ONLY, 11, 2),
        "p:big_no_ext": (TableSyncMode.EXTERNAL_ONLY, 11, 0),
        "p:small": (TableSyncMode.FULL, 10, 10),
    }
    mock_cleanup.assert_called_once()


@patch.object(bigquery_table, "_set_dataset_table_sync_status")
@patch.object(bigquery_table, "cleanup_bigquery_tables")
@patch.object(bigquery_table, "load_bigquery_tables")
@patch.object(bigquery_table, "get_bigquery_table_detail")
@patch.object(bigquery_table, "get_bigquery_tables")
def test_sync_tables_marks_failed_listing(
    mock_get_tables, mock_get_detail, mock_load, mock_cleanup, mock_status
):
    mock_get_tables.return_value = None

    bigquery_table.sync_bigquery_tables(
        MagicMock(), _client(), [_dataset("denied")], "p", 1, {"UPDATE_TAG": 1}
    )

    mock_load.assert_not_called()
    mock_get_detail.assert_not_called()
    assert _status(mock_status) == {"p:denied": (TableSyncMode.FAILED, None, 0)}
