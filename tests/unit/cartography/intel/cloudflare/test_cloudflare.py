from itertools import repeat
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest
from cloudflare.pagination import V4PagePaginationArrayResultInfo

import cartography.intel.cloudflare
import cartography.intel.cloudflare.accounts as accounts
from cartography.config import Config
from cartography.intel.cloudflare import start_cloudflare_ingestion
from tests.data.cloudflare.accounts import CLOUDFLARE_ACCOUNTS


def _page(records, result_info):
    # Mimic SyncV4PagePaginationArray: exposes .result and .result_info, and is
    # iterable. Iteration is unbounded (repeats the first item) to reproduce the
    # /accounts paginator hang - get() must NOT iterate it. result_info is a real
    # V4PagePaginationArrayResultInfo so total_count presence/absence matches the
    # SDK contract (the model declares only page/per_page; total_count resolves
    # via extra="allow" only when the response includes it).
    objs = [MagicMock(to_dict=lambda r=r: r) for r in records]
    page = MagicMock()
    page.result = objs
    page.result_info = result_info
    page.__iter__ = lambda self: repeat(objs[0])
    return page


def _result_info(**fields):
    return V4PagePaginationArrayResultInfo.model_validate({"page": 1, **fields})


TEST_UPDATE_TAG = 123456789
ACCOUNT_ID = "37418d7e-710b-4aa0-a4c0-79ee660690bf"
ZONE_ID = "be68b067-5b2b-49f7-ad89-943d501dc900"


def test_start_cloudflare_ingestion_requires_token():
    config = Config(neo4j_uri="bolt://localhost:7687")

    with pytest.raises(RuntimeError, match="Cloudflare import is not configured"):
        start_cloudflare_ingestion(None, config)


@patch.object(cartography.intel.cloudflare.rulesets, "sync")
@patch.object(cartography.intel.cloudflare.workerroutes, "sync")
@patch.object(cartography.intel.cloudflare.workerscripts, "sync")
@patch.object(cartography.intel.cloudflare.r2buckets, "get", return_value=([], False))
@patch.object(cartography.intel.cloudflare.dnsrecords, "sync")
@patch.object(cartography.intel.cloudflare.dnsrecords, "migrate_account_resource_edges")
@patch.object(
    cartography.intel.cloudflare.zones, "sync", return_value=[{"id": ZONE_ID}]
)
@patch.object(cartography.intel.cloudflare.members, "sync")
@patch.object(cartography.intel.cloudflare.roles, "sync")
@patch.object(
    cartography.intel.cloudflare.accounts, "sync", return_value=[{"id": ACCOUNT_ID}]
)
@patch("cartography.intel.cloudflare.Cloudflare")
def test_r2_listing_failure_does_not_stop_the_later_stages(
    mock_client_class,
    mock_accounts,
    mock_roles,
    mock_members,
    mock_zones,
    mock_migrate,
    mock_dnsrecords,
    mock_r2_get,
    mock_workerscripts,
    mock_workerroutes,
    mock_rulesets,
):
    """
    Ensure that a refused R2 bucket listing does not abort the ingestion: the
    Workers and ruleset stages still run, as the module config documents.
    """

    # Arrange
    config = Config(
        neo4j_uri="bolt://localhost:7687",
        cloudflare_token="fake-token",
        update_tag=TEST_UPDATE_TAG,
    )

    # Act: r2buckets.sync runs for real and returns early on the failed listing
    start_cloudflare_ingestion(MagicMock(), config)

    # Assert the stages that come after R2 still ran
    mock_r2_get.assert_called_once()
    mock_workerscripts.assert_called_once()
    mock_workerroutes.assert_called_once()
    mock_rulesets.assert_called_once()


def test_get_accounts_reads_single_page_not_paginator() -> None:
    # Regression: the /accounts endpoint ignores `page` and echoes the same
    # non-empty page forever, and the SDK paginator stops only on an empty page
    # -> iterating it never terminates. get() must read a single page (at the
    # max page size) via .result. See cloudflare/cloudflare-python#2584.
    client = MagicMock()
    client.accounts.list.return_value = _page(
        CLOUDFLARE_ACCOUNTS,
        _result_info(total_count=len(CLOUDFLARE_ACCOUNTS)),
    )

    result = accounts.get(client)

    assert result == CLOUDFLARE_ACCOUNTS
    client.accounts.list.assert_called_once_with(
        per_page=accounts.MAX_ACCOUNTS_PER_PAGE
    )


def test_get_accounts_at_page_limit_does_not_raise() -> None:
    # Boundary: total_count == returned count (no overflow) must not raise.
    client = MagicMock()
    client.accounts.list.return_value = _page(
        CLOUDFLARE_ACCOUNTS,
        _result_info(total_count=len(CLOUDFLARE_ACCOUNTS)),
    )

    assert accounts.get(client) == CLOUDFLARE_ACCOUNTS


def test_get_accounts_empty() -> None:
    # No accounts: return [] rather than raising.
    client = MagicMock()
    client.accounts.list.return_value = _page([], _result_info(total_count=0))

    assert accounts.get(client) == []


def test_get_accounts_raises_when_accounts_exceed_one_page() -> None:
    # >MAX_ACCOUNTS_PER_PAGE accounts are unreachable via /accounts (page is
    # ignored, no cursor). get() must fail loudly rather than silently drop the
    # overflow.
    client = MagicMock()
    client.accounts.list.return_value = _page(
        CLOUDFLARE_ACCOUNTS,
        _result_info(total_count=len(CLOUDFLARE_ACCOUNTS) + 60),
    )

    with pytest.raises(RuntimeError, match="partial account set"):
        accounts.get(client)


def test_get_accounts_without_total_count_does_not_crash() -> None:
    # V4PagePaginationArrayResultInfo declares only page/per_page. When the
    # response omits total_count, accessing it raises AttributeError - get() must
    # tolerate that and return the page it has rather than crash.
    client = MagicMock()
    client.accounts.list.return_value = _page(
        CLOUDFLARE_ACCOUNTS,
        _result_info(),  # no total_count
    )

    result = accounts.get(client)

    assert result == CLOUDFLARE_ACCOUNTS


def test_get_accounts_with_none_result_info_does_not_crash() -> None:
    # result_info itself is Optional; get() must handle None.
    client = MagicMock()
    client.accounts.list.return_value = _page(CLOUDFLARE_ACCOUNTS, None)

    result = accounts.get(client)

    assert result == CLOUDFLARE_ACCOUNTS
