from unittest.mock import MagicMock

import pytest

from cartography.intel.crowdstrike.spotlight import build_spotlight_filter
from cartography.intel.crowdstrike.spotlight import get_spotlight_vulnerabilities
from cartography.intel.crowdstrike.spotlight import SPOTLIGHT_FILTER


@pytest.mark.parametrize("extra", [None, "", "   "])
def test_no_extra_filter_keeps_default(extra):
    assert build_spotlight_filter(extra) == SPOTLIGHT_FILTER


def test_extra_filter_is_anded_onto_status():
    extra = "cve.severity:['CRITICAL','HIGH']+cve.exprt_rating:['HIGH','CRITICAL']"
    assert build_spotlight_filter(extra) == f"{SPOTLIGHT_FILTER}+{extra}"


def test_leading_plus_is_not_doubled():
    assert build_spotlight_filter("+cve.severity:'CRITICAL'") == (
        f"{SPOTLIGHT_FILTER}+cve.severity:'CRITICAL'"
    )


def test_filter_is_sent_to_the_api():
    client = MagicMock()
    client.combinedQueryVulnerabilities.return_value = {
        "status_code": 200,
        "body": {"resources": [], "meta": {"pagination": {}}},
    }
    flt = build_spotlight_filter("cve.severity:'CRITICAL'")
    list(get_spotlight_vulnerabilities(client, flt))
    sent = client.combinedQueryVulnerabilities.call_args.kwargs["parameters"]
    assert sent["filter"] == flt
