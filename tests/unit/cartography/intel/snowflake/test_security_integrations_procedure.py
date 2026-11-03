import json
import logging
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from cartography.intel.snowflake import security_integrations as si
from cartography.intel.snowflake.util import SnowflakeSqlError

SHOW = [
    {
        "name": "OKTAINTEGRATION",
        "type": "SAML2",
        "category": "SECURITY",
        "enabled": "true",
    },
    {
        "name": "OKTA_PROVISIONING",
        "type": "SCIM - OKTA",
        "category": "SECURITY",
        "enabled": "true",
    },
    {
        "name": "SNOWFLAKE$LOCAL_APPLICATION",
        "type": "OAUTH - LOCAL_APPLICATION",
        "category": "SECURITY",
        "enabled": "true",
    },
]

PAYLOAD = {
    "version": 1,
    "integrations": [
        {
            "name": "OKTAINTEGRATION",
            "properties": {
                "SAML2_ISSUER": {
                    "type": "String",
                    "value": "http://www.okta.com/exk",
                    "default": "",
                },
                "SAML2_PROVIDER": {"type": "String", "value": "OKTA", "default": ""},
            },
            "error": None,
        },
        {
            "name": "OKTA_PROVISIONING",
            "properties": {
                "RUN_AS_ROLE": {
                    "type": "String",
                    "value": "OKTA_PROVISIONER",
                    "default": "",
                },
            },
            "error": None,
        },
        {
            "name": "SNOWFLAKE$LOCAL_APPLICATION",
            "properties": None,
            "error": "SnowparkSQLException: Insufficient privileges",
        },
    ],
}


def _client(call_result=None, call_error=None):
    client = MagicMock()
    client.account_id = "ORG.ACCT"

    def run_sql(statement, *args, **kwargs):
        if statement.startswith("CALL "):
            if call_error:
                raise call_error
            return call_result
        if statement == "SHOW SECURITY INTEGRATIONS":
            return SHOW
        raise AssertionError(statement)

    client.run_sql.side_effect = run_sql
    return client


def _procedure_rows(payload):
    # The SQL API returns a VARIANT column as JSON text.
    return [{"describe_security_integrations": json.dumps(payload)}]


def test_parse_procedure_output_folds_like_describe():
    details = si.parse_procedure_output(json.dumps(PAYLOAD))
    assert details == {
        "OKTAINTEGRATION": {
            "saml2_issuer": "http://www.okta.com/exk",
            "saml2_provider": "OKTA",
        },
        "OKTA_PROVISIONING": {"run_as_role": "OKTA_PROVISIONER"},
    }


@pytest.mark.parametrize(
    "raw",
    [
        json.dumps({"version": 2, "integrations": []}),
        json.dumps({"integrations": []}),
        json.dumps({"version": 1}),
        json.dumps([1, 2]),
        "not json",
        None,
    ],
)
def test_parse_procedure_output_rejects_bad_payloads(raw):
    with pytest.raises(ValueError):
        si.parse_procedure_output(raw)


@pytest.mark.parametrize(
    "message",
    [
        "Snowflake error 002003: SQL compilation error: Database 'CARTOGRAPHY_SUPPORT' does not exist or not authorized.",
        "Snowflake error 002141: SQL compilation error: Unknown user-defined function CARTOGRAPHY_SUPPORT.PROCS.DESCRIBE_SECURITY_INTEGRATIONS",
        "Snowflake error 003001: SQL access control error: Insufficient privileges to operate on procedure",
    ],
)
def test_not_deployed_is_silent(message, caplog):
    client = _client(call_error=SnowflakeSqlError(message))
    with caplog.at_level(logging.INFO, logger=si.logger.name):
        assert si.get_details_from_procedure(client) is None
    assert [r.levelno for r in caplog.records] == [logging.INFO]


def test_deployed_but_broken_warns_and_does_not_raise(caplog):
    client = _client(
        call_error=SnowflakeSqlError("Snowflake error 100357: Python Interpreter Error")
    )
    with caplog.at_level(logging.INFO, logger=si.logger.name):
        assert si.get_details_from_procedure(client) is None
    assert [r.levelno for r in caplog.records] == [logging.WARNING]


@pytest.mark.parametrize(
    "rows", [[], [{"x": "not json"}], _procedure_rows({"version": 99})]
)
def test_unusable_result_warns_and_does_not_raise(rows, caplog):
    with caplog.at_level(logging.INFO, logger=si.logger.name):
        assert si.get_details_from_procedure(_client(call_result=rows)) is None
    assert [r.levelno for r in caplog.records] == [logging.WARNING]


def test_disabled_procedure_is_not_called():
    client = _client()
    assert si.get_details_from_procedure(client, None) is None
    client.run_sql.assert_not_called()


@patch.object(si, "load_security_integrations")
@patch.object(si, "get_details")
def test_sync_without_procedure_matches_old_behaviour(mock_get_details, mock_load):
    # Today: the procedure doesn't exist and DESC is refused for every integration.
    mock_get_details.return_value = None
    client = _client(
        call_error=SnowflakeSqlError(
            "Database 'CARTOGRAPHY_SUPPORT' does not exist or not authorized."
        )
    )

    complete = si.sync(MagicMock(), client, {"UPDATE_TAG": 1})

    assert complete is False
    assert mock_get_details.call_count == len(SHOW)
    assert mock_load.call_args.args[1] == []


@patch.object(si, "load_security_integrations")
@patch.object(si, "get_details")
def test_sync_with_procedure_loads_and_falls_back_per_integration(
    mock_get_details, mock_load
):
    mock_get_details.return_value = None  # direct DESC still refused
    client = _client(call_result=_procedure_rows(PAYLOAD))

    complete = si.sync(MagicMock(), client, {"UPDATE_TAG": 1})

    # Only the integration the procedure couldn't describe falls back to DESC.
    mock_get_details.assert_called_once_with(client, "SNOWFLAKE$LOCAL_APPLICATION")
    assert complete is False
    loaded = {row["name"]: row for row in mock_load.call_args.args[1]}
    assert set(loaded) == {"OKTAINTEGRATION", "OKTA_PROVISIONING"}
    assert loaded["OKTAINTEGRATION"]["saml2_issuer"] == "http://www.okta.com/exk"
    assert loaded["OKTAINTEGRATION"]["protocol"] == "SAML"
    assert loaded["OKTA_PROVISIONING"]["run_as_role"] == "OKTA_PROVISIONER"
    assert loaded["OKTA_PROVISIONING"]["run_as_role_id"].endswith(
        "/role/OKTA_PROVISIONER"
    )


@patch.object(si, "load_security_integrations")
@patch.object(si, "get_details")
def test_sync_complete_when_procedure_covers_everything(mock_get_details, mock_load):
    payload = {
        "version": 1,
        "integrations": [{"name": row["name"], "properties": {}} for row in SHOW],
    }
    client = _client(call_result=_procedure_rows(payload))

    assert si.sync(MagicMock(), client, {"UPDATE_TAG": 1}) is True
    mock_get_details.assert_not_called()
    assert len(mock_load.call_args.args[1]) == len(SHOW)
