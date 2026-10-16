from cartography.intel.snowflake.cortex_search_services import transform

TEST_ACCOUNT_ID = "MYORG.MYACCT"


def test_transform_flattens_target_lag_dict_to_text():
    """target_lag must go through schedule_to_text() like every other Snowflake
    schedule-shaped field, not be passed through to Neo4j as a raw dict.

    Confirmed against a real account (2026-10-16): Snowflake's object API
    returns a Cortex Search service's target_lag as a nested object
    (`{"type": "USER_DEFINED", "seconds": 1800}`), not the plain string this
    module's own test fixture data (tests/data/snowflake/cortex_search_services.py)
    had always used - which is why neither the unit nor integration test suite
    caught this before a real sync hit it. Neo4j rejects a raw dict property
    outright (Neo.ClientError.Statement.TypeError /
    neo4j.exceptions.GqlError 22N01 "Expected ... to be of type ... but was of
    type MAP NOT NULL"), aborting the whole Snowflake sync. dynamic_tables.py
    already calls schedule_to_text() on the identical target_lag field; this
    module was the one call site missing it.
    """
    schema = {
        "id": "MYORG.MYACCT/schema/PROD.PUBLIC",
        "database_name": "PROD",
        "name": "PUBLIC",
    }
    services = [
        {
            "name": "SEARCH_SVC",
            "target_lag": {"type": "USER_DEFINED", "seconds": 1800},
            "warehouse": "WH1",
            "source": "PROD.PUBLIC.DOCS",
            "embedding_model": "snowflake-arctic-embed-m-v1.5",
            "attribute_columns": None,
            "search_column": "TEXT",
            "service_query_url": None,
            "comment": None,
            "created_on": "2026-08-03T17:20:00.000+00:00",
        },
    ]

    result = transform(services, schema, TEST_ACCOUNT_ID)

    assert len(result) == 1
    assert result[0]["target_lag"] == "1800 seconds"
    # Every value must be a primitive (or None) - the exact class of value
    # Neo4j rejected in the real failure this test guards against.
    for key, value in result[0].items():
        assert not isinstance(value, dict), f"{key!r} is a dict: {value!r}"


def test_transform_passes_through_cron_style_target_lag():
    """A cron-form target_lag (schedule_to_text's other branch) also flattens
    to a single string, matching the DOWNSTREAM cron-schedule tasks/alerts
    already exercise for the `schedule` field.
    """
    schema = {
        "id": "MYORG.MYACCT/schema/PROD.PUBLIC",
        "database_name": "PROD",
        "name": "PUBLIC",
    }
    services = [
        {
            "name": "SEARCH_SVC",
            "target_lag": {"cron": "0 0 * * *", "time_zone": "UTC"},
            "warehouse": None,
            "source": None,
            "embedding_model": "snowflake-arctic-embed-m-v1.5",
            "attribute_columns": None,
            "search_column": "TEXT",
            "service_query_url": None,
            "comment": None,
            "created_on": None,
        },
    ]

    result = transform(services, schema, TEST_ACCOUNT_ID)

    assert result[0]["target_lag"] == "USING CRON 0 0 * * * UTC"


def test_transform_handles_missing_target_lag():
    """A service with no target_lag at all (absent key) does not crash and
    yields None, matching schedule_to_text()'s documented behavior for an
    empty/missing schedule.
    """
    schema = {
        "id": "MYORG.MYACCT/schema/PROD.PUBLIC",
        "database_name": "PROD",
        "name": "PUBLIC",
    }
    services = [
        {
            "name": "SEARCH_SVC",
            "warehouse": None,
            "source": None,
            "embedding_model": "snowflake-arctic-embed-m-v1.5",
            "attribute_columns": None,
            "search_column": "TEXT",
            "service_query_url": None,
            "comment": None,
            "created_on": None,
        },
    ]

    result = transform(services, schema, TEST_ACCOUNT_ID)

    assert result[0]["target_lag"] is None
