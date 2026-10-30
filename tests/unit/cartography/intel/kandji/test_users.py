from typing import Any
from unittest.mock import Mock
from unittest.mock import patch

import tests.data.kandji.users
from cartography.intel.kandji.devices import transform as transform_devices
from cartography.intel.kandji.users import get
from cartography.intel.kandji.users import transform


def _page(results, next_url=None):
    return Mock(
        json=lambda: {"next": next_url, "previous": None, "results": results},
        raise_for_status=lambda: None,
    )


@patch("cartography.intel.kandji.users.Session")
def test_get_users_follows_next_cursor(mock_session: Mock) -> None:
    # Arrange
    page2_url = "https://test.kandji.io/api/v1/users?cursor=abc"
    mock_session.return_value.get.side_effect = [
        _page([{"id": "u1"}, {"id": "u2"}], next_url=page2_url),
        _page([{"id": "u3"}]),
    ]

    # Act
    result = get("https://test.kandji.io", "test-token")

    # Assert
    assert [u["id"] for u in result] == ["u1", "u2", "u3"]
    calls = mock_session.return_value.get.call_args_list
    assert calls[0].args[0] == "https://test.kandji.io/api/v1/users"
    assert calls[1].args[0] == page2_url


@patch("cartography.intel.kandji.users.Session")
def test_get_users_empty(mock_session: Mock) -> None:
    mock_session.return_value.get.side_effect = [_page([])]

    assert get("https://test.kandji.io", "test-token") == []
    assert mock_session.return_value.get.call_count == 1


def test_transform_users() -> None:
    raw = (
        tests.data.kandji.users.USERS["simpson_corp_users"]
        + tests.data.kandji.users.USERS["south_park_users"]
    )

    result = transform(raw)

    homer = result[0]
    assert homer["id"] == "0a1b2c3d-1111-4a4a-8888-000000000001"
    assert homer["email"] == "hjsimpson@simpson.corp"
    assert homer["integration_type"] == "scim"
    assert homer["device_count"] == 1
    assert "integration" not in homer
    assert "deprecated_user_id" not in homer

    # integration may be null in the API response
    cartman = result[2]
    assert cartman["integration_type"] is None
    assert cartman["archived"] is True


def test_transform_devices_flattens_user_id() -> None:
    raw: list[dict[str, Any]] = [
        {
            "device_id": "d1",
            "user": {
                "id": "u1",
                "email": "a@b.c",
                "name": "A",
                "is_archived": False,
                "active": True,
            },
        },
        # Unassigned devices come back with an empty user value
        {"device_id": "d2", "user": ""},
        {"device_id": "d3", "user": None},
        {"device_id": "d4"},
    ]

    result = transform_devices(raw)

    assert [d["user_id"] for d in result] == ["u1", None, None, None]
    assert [d["id"] for d in result] == ["d1", "d2", "d3", "d4"]
