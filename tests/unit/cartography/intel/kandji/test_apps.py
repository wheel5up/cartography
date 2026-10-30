from typing import Any
from unittest.mock import Mock
from unittest.mock import patch

import pytest

import tests.data.kandji.apps
from cartography.intel.kandji import apps as kandji_apps
from cartography.intel.kandji.apps import get
from cartography.intel.kandji.apps import transform


def _resp(status: int, apps: list[dict[str, Any]] | None = None, headers=None):
    r = Mock()
    r.status_code = status
    r.headers = headers or {}
    r.json.return_value = {"device_id": "x", "apps": apps or []}
    r.raise_for_status.side_effect = (
        None if status < 400 else Exception(f"HTTP {status}")
    )
    return r


def test_transform_dedupes_apps_and_keeps_one_install_per_device_version() -> None:
    data = {
        dev: list(apps) for dev, apps in tests.data.kandji.apps.APPS_BY_DEVICE.items()
    }
    # A duplicate copy of Chrome at a second path on the same device
    first_device = next(iter(data))
    data[first_device].append(
        dict(data[first_device][0], path="/Users/test/Applications/Google Chrome.app"),
    )
    # An entry with no bundle_id is dropped
    data[first_device].append({"app_name": "No Bundle", "version": "1"})

    apps, installs = transform(data)

    assert {a["id"] for a in apps} == {
        "com.google.Chrome",
        "com.apple.Safari",
        "com.example.tool",
    }
    chrome = next(a for a in apps if a["id"] == "com.google.Chrome")
    assert chrome["name"] == "Google Chrome"

    pairs = {(i["device_id"], i["id"]) for i in installs}
    assert pairs == {
        ("fc60decb-30cb-4db1-b3ec-2fa8ea1b83ed", "com.google.Chrome:148.0.7778.168"),
        ("fc60decb-30cb-4db1-b3ec-2fa8ea1b83ed", "com.apple.Safari:26.6.2"),
        ("f27bcd08-f653-4930-83b0-51970e923b98", "com.google.Chrome:156.0.8078.74"),
        ("f27bcd08-f653-4930-83b0-51970e923b98", "com.example.tool:0.1"),
    }
    # First occurrence wins for per-install fields
    stale = next(i for i in installs if i["id"] == "com.google.Chrome:148.0.7778.168")
    assert stale["path"] == "/Applications/Google Chrome.app"
    assert stale["source"] == "Identified Developer"
    assert stale["modification_date"] == "2026-05-20T10:00:00Z"


def test_transform_missing_version_uses_empty_string() -> None:
    apps, installs = transform({"d1": [{"bundle_id": "com.x", "app_name": "X"}]})
    assert installs[0]["id"] == "com.x:"
    assert installs[0]["version"] == ""


@patch("cartography.intel.kandji.apps.Session")
def test_get_fetches_each_device(mock_session: Mock) -> None:
    mock_session.return_value.get.side_effect = lambda url, **kw: _resp(
        200,
        [{"bundle_id": url.rsplit("/", 2)[-2]}],
    )

    result = get("https://t.kandji.io", "tok", ["d1", "d2", "d3"])

    assert set(result) == {"d1", "d2", "d3"}
    assert result["d2"] == [{"bundle_id": "d2"}]
    urls = sorted(c.args[0] for c in mock_session.return_value.get.call_args_list)
    assert urls == [
        "https://t.kandji.io/api/v1/devices/d1/apps",
        "https://t.kandji.io/api/v1/devices/d2/apps",
        "https://t.kandji.io/api/v1/devices/d3/apps",
    ]


@patch("cartography.intel.kandji.apps.time.sleep")
@patch("cartography.intel.kandji.apps.Session")
def test_get_retries_on_429_then_succeeds(mock_session: Mock, mock_sleep: Mock) -> None:
    mock_session.return_value.get.side_effect = [
        _resp(429, headers={"Retry-After": "2"}),
        _resp(503),
        _resp(200, [{"bundle_id": "com.ok"}]),
    ]

    result = get("https://t.kandji.io", "tok", ["d1"])

    assert result == {"d1": [{"bundle_id": "com.ok"}]}
    # Retry-After honoured, then exponential backoff (attempt 1 -> 2s)
    assert [c.args[0] for c in mock_sleep.call_args_list] == [2.0, 2.0]


@patch("cartography.intel.kandji.apps.time.sleep")
@patch("cartography.intel.kandji.apps.Session")
def test_get_raises_after_max_retries(mock_session: Mock, mock_sleep: Mock) -> None:
    mock_session.return_value.get.side_effect = lambda *a, **kw: _resp(429)

    with pytest.raises(Exception, match="HTTP 429"):
        get("https://t.kandji.io", "tok", ["d1"])
    assert mock_sleep.call_count == kandji_apps._MAX_RETRIES


@patch("cartography.intel.kandji.apps.Session")
def test_get_treats_404_as_no_apps(mock_session: Mock) -> None:
    mock_session.return_value.get.side_effect = lambda *a, **kw: _resp(404)

    assert get("https://t.kandji.io", "tok", ["gone"]) == {"gone": []}
