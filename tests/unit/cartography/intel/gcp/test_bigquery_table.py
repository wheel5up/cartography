from unittest.mock import MagicMock
from unittest.mock import patch

import cartography.intel.gcp.bigquery_table as bigquery_table


def _dataset(dataset_id):
    return {"datasetReference": {"datasetId": dataset_id}}


def _tables(dataset_id, n):
    return [
        {
            "tableReference": {"datasetId": dataset_id, "tableId": f"t{i}"},
            "type": "TABLE",
        }
        for i in range(n)
    ]


@patch.object(bigquery_table, "cleanup_bigquery_tables")
@patch.object(bigquery_table, "load_bigquery_tables")
@patch.object(bigquery_table, "get_bigquery_table_detail")
@patch.object(bigquery_table, "get_bigquery_tables")
def test_sync_tables_fetches_details_concurrently_in_order(
    mock_get_tables, mock_get_detail, mock_load, mock_cleanup
):
    mock_get_tables.side_effect = lambda client, project_id, dataset_id: _tables(
        dataset_id, 50
    )
    mock_get_detail.side_effect = (
        lambda client, project_id, dataset_id, table_id, http=None: {
            "numRows": f"{dataset_id}.{table_id}"
        }
    )
    client = MagicMock()
    client._http = None

    bigquery_table.sync_bigquery_tables(
        MagicMock(), client, [_dataset("ds")], "p", 1, {"UPDATE_TAG": 1}
    )

    assert mock_get_detail.call_count == 50
    loaded = mock_load.call_args.args[1]
    assert [t["table_id"] for t in loaded] == [f"t{i}" for i in range(50)]
    assert [t["num_rows"] for t in loaded] == [f"ds.t{i}" for i in range(50)]


@patch.object(bigquery_table, "cleanup_bigquery_tables")
@patch.object(bigquery_table, "load_bigquery_tables")
@patch.object(bigquery_table, "get_bigquery_table_detail")
@patch.object(bigquery_table, "get_bigquery_tables")
def test_sync_tables_skips_details_for_hidden_datasets(
    mock_get_tables, mock_get_detail, mock_load, mock_cleanup
):
    mock_get_tables.side_effect = lambda client, project_id, dataset_id: _tables(
        dataset_id, 3
    )
    mock_get_detail.return_value = {"numRows": "7"}
    client = MagicMock()
    client._http = None

    bigquery_table.sync_bigquery_tables(
        MagicMock(),
        client,
        [_dataset("_0123abcd"), _dataset("visible")],
        "p",
        1,
        {"UPDATE_TAG": 1},
    )

    called_datasets = {c.args[2] for c in mock_get_detail.call_args_list}
    assert called_datasets == {"visible"}
    loaded = mock_load.call_args.args[1]
    # Hidden dataset tables are still inventoried, just without detail fields.
    assert {(t["dataset_id"], t["num_rows"]) for t in loaded} == {
        ("p:_0123abcd", None),
        ("p:visible", "7"),
    }
    assert len(loaded) == 6
