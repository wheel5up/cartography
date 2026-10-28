import pytest

from cartography.intel.snowflake.databases import _skip_walk_reason
from cartography.intel.snowflake.databases import matches_any


@pytest.mark.parametrize(
    "name",
    [
        "DROPPED_USER$AHENDERSON@G2.COM_1788162201",
        # As Snowflake actually reports it: wrapped in literal quotes.
        '"DROPPED_USER$AHENDERSON@G2.COM_1788162201"',
        "DROPPED_USER$TEST_USER_1784055018",
    ],
)
def test_dropped_user_placeholders_are_never_walked(name):
    assert _skip_walk_reason({"name": name}, None) is not None
    # Even an explicit allowlist does not pull them back in.
    assert _skip_walk_reason({"name": name}, {name}) is not None


def test_operator_skip_list_wins_over_allowlist():
    database = {"name": "MARKETING"}
    assert _skip_walk_reason(database, {"MARKETING"}, frozenset({"marketing"}))
    assert _skip_walk_reason(database, None, frozenset({"MARK*"}))
    assert _skip_walk_reason(database, None, frozenset({"OTHER"})) is None


def test_ordinary_databases_are_walked():
    assert _skip_walk_reason({"name": "GDC"}, None) is None
    # The DROPPED_USER pattern is anchored; a database merely containing it is walked.
    assert _skip_walk_reason({"name": "MY_DROPPED_USER$X"}, None) is None


def test_matches_any_strips_reported_quotes_per_component():
    # Schema names come back quoted too; the dot inside the quotes is not a split.
    name = 'SNOWFLAKE_LEARNING_DB."GMCFAUL@G2.COM_getting_started"'
    assert matches_any(name, frozenset({"SNOWFLAKE_LEARNING_DB.GMCFAUL@*"}))
    assert matches_any(name, frozenset({"snowflake_learning_db.*@*"}))
    # Matching the raw (quoted) form still works for anyone who wrote it that way.
    assert matches_any(name, frozenset({'SNOWFLAKE_LEARNING_DB."GMCFAUL*'}))
    assert not matches_any(name, frozenset({"OTHER_DB.*"}))


def test_matches_any_is_case_insensitive_glob():
    assert matches_any("Business_Analytics", frozenset({"BUSINESS_*"}))
    assert not matches_any("GDC", frozenset({"GDC_*"}))
    assert not matches_any("GDC", frozenset())
