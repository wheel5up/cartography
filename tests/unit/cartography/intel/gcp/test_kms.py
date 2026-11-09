import json
import logging
from unittest.mock import MagicMock

from googleapiclient.errors import HttpError

from cartography.intel.gcp.kms import get_kms_locations


def _make_http_error(status: int, payload: dict) -> HttpError:
    resp = MagicMock()
    resp.status = status
    return HttpError(resp=resp, content=json.dumps(payload).encode("utf-8"))


def test_get_kms_locations_api_disabled_logs_concisely(monkeypatch, caplog):
    client = MagicMock()
    request = MagicMock()
    client.projects.return_value.locations.return_value.list.return_value = request

    error = _make_http_error(
        403,
        {
            "error": {
                "message": "Cloud KMS API has not been used in project 123 before or it is disabled",
                "details": [
                    {
                        "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                        "reason": "SERVICE_DISABLED",
                    }
                ],
            }
        },
    )

    monkeypatch.setattr(
        "cartography.intel.gcp.kms.gcp_api_execute_with_retry",
        lambda _request: (_ for _ in ()).throw(error),
    )

    with caplog.at_level(logging.WARNING):
        locations = get_kms_locations(client, "test-project")

    assert locations is None
    assert "HTTP 403 SERVICE_DISABLED" in caplog.text
    assert "googleapiclient.errors.HttpError" not in caplog.text


def _request(response=None, error=None):
    request = MagicMock()
    if error is not None:
        request.execute.side_effect = error
    else:
        request.execute.return_value = response
    return request


def _client_with_rings(rings_by_parent):
    """A KMS client whose keyRings().list(parent=...) returns one page per parent."""
    client = MagicMock()
    # No real AuthorizedHttp on the mock, so get_key_rings uses each request's own http.
    client._http = None
    key_rings = (
        client.projects.return_value.locations.return_value.keyRings.return_value
    )
    key_rings.list.side_effect = lambda parent: rings_by_parent[parent]
    key_rings.list_next.return_value = None
    return client, key_rings


def test_get_key_rings_queries_every_location_and_keeps_order():
    from cartography.intel.gcp.kms import get_key_rings

    locations = [{"locationId": f"loc-{i}"} for i in range(40)] + [{}]
    rings_by_parent = {
        f"projects/p/locations/loc-{i}": _request(
            {"keyRings": [{"name": f"projects/p/locations/loc-{i}/keyRings/r"}]}
            if i % 10 == 0
            else {}
        )
        for i in range(40)
    }
    client, key_rings = _client_with_rings(rings_by_parent)

    rings = get_key_rings(client, "p", locations)

    assert key_rings.list.call_count == 40  # location without an id is skipped
    assert [r["name"] for r in rings] == [
        f"projects/p/locations/loc-{i}/keyRings/r" for i in (0, 10, 20, 30)
    ]


def test_get_key_rings_any_location_api_disabled_skips_whole_sync():
    from cartography.intel.gcp.kms import get_key_rings

    disabled = _make_http_error(
        403,
        {
            "error": {
                "message": "Cloud KMS API has not been used in project 123",
                "details": [
                    {
                        "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                        "reason": "SERVICE_DISABLED",
                    }
                ],
            }
        },
    )
    rings_by_parent = {
        "projects/p/locations/a": _request({"keyRings": [{"name": "x"}]}),
        "projects/p/locations/b": _request(error=disabled),
    }
    client, _ = _client_with_rings(rings_by_parent)

    assert (
        get_key_rings(client, "p", [{"locationId": "a"}, {"locationId": "b"}]) is None
    )
