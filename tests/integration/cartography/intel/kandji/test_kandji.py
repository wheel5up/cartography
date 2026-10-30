import logging

import cartography.intel.kandji
import cartography.intel.kandji.users
import tests.data.kandji.devices
import tests.data.kandji.tenant
import tests.data.kandji.users
from tests.integration.util import check_nodes
from tests.integration.util import check_rels

logger = logging.getLogger(__name__)


def test_load_kandji_devices_relationship(neo4j_session):
    # Arrange
    TEST_UPDATE_TAG = 1234
    TEST_KANDJI_TENANT_ID = tests.data.kandji.tenant.TENANT["simpson_corp"]["id"]
    common_job_parameters = {
        "UPDATE_TAG": TEST_UPDATE_TAG,
        "TENANT_ID": TEST_KANDJI_TENANT_ID,
    }
    data = tests.data.kandji.devices.DEVICES["simpson_corp_devices"]

    # Act
    cartography.intel.kandji.devices.load_devices(
        neo4j_session,
        common_job_parameters,
        data,
    )

    # Assert

    # Make sure the expected Tenant is created
    expected_nodes = {
        ("SimpsonCorp",),
    }
    assert (
        check_nodes(
            neo4j_session,
            "KandjiTenant",
            ["id"],
        )
        == expected_nodes
    )

    # Make sure the expected Devices are created
    expected_nodes = {
        ("fc60decb-30cb-4db1-b3ec-2fa8ea1b83ed",),
        ("f27bcd08-f653-4930-83b0-51970e923b98",),
    }
    assert (
        check_nodes(
            neo4j_session,
            "KandjiDevice",
            ["id"],
        )
        == expected_nodes
    )

    # Make sure the expected relationships are created
    expected_nodes_relationships = {
        ("SimpsonCorp", "fc60decb-30cb-4db1-b3ec-2fa8ea1b83ed"),
        ("SimpsonCorp", "f27bcd08-f653-4930-83b0-51970e923b98"),
    }
    assert (
        check_rels(
            neo4j_session,
            "KandjiTenant",
            "id",
            "KandjiDevice",
            "id",
            "RESOURCE",
            rel_direction_right=True,
        )
        == expected_nodes_relationships
    )

    # Cleanup test data
    common_job_parameters = {
        "UPDATE_TAG": TEST_UPDATE_TAG + 1234,
        "TENANT_ID": TEST_KANDJI_TENANT_ID,
    }
    cartography.intel.kandji.devices.cleanup(
        neo4j_session,
        common_job_parameters,
    )


def test_cleanup_kandji_devices(neo4j_session):
    # Arrange
    TEST_UPDATE_TAG = 1234
    TEST_KANDJI_TENANT_ID = tests.data.kandji.tenant.TENANT["simpson_corp"]["id"]
    common_job_parameters = {
        "UPDATE_TAG": TEST_UPDATE_TAG,
        "TENANT_ID": TEST_KANDJI_TENANT_ID,
    }
    data = tests.data.kandji.devices.DEVICES["simpson_corp_devices"]

    # Act
    cartography.intel.kandji.devices.load_devices(
        neo4j_session,
        common_job_parameters,
        data,
    )

    # Arrange: load in an unrelated data with different UPDATE_TAG
    UNRELATED_UPDATE_TAG = TEST_UPDATE_TAG + 1
    TENANT_ID = tests.data.kandji.tenant.TENANT["south_park"]["id"]
    common_job_parameters = {
        "UPDATE_TAG": UNRELATED_UPDATE_TAG,
        "TENANT_ID": TENANT_ID,
    }
    data = tests.data.kandji.devices.DEVICES["south_park_devices"]

    cartography.intel.kandji.devices.load_devices(
        neo4j_session,
        common_job_parameters,
        data,
    )

    # # [Pre-test] Assert

    # [Pre-test] Assert that the unrelated data exists
    expected_nodes_relationships = {
        ("SimpsonCorp", "fc60decb-30cb-4db1-b3ec-2fa8ea1b83ed"),
        ("SimpsonCorp", "f27bcd08-f653-4930-83b0-51970e923b98"),
        ("SouthPark", "748C5E49-134E-486C-A609-88A66A1BE4A1"),
        ("SouthPark", "706AF44A-9F51-4E84-B336-C4924097FFB6"),
    }
    assert (
        check_rels(
            neo4j_session,
            "KandjiTenant",
            "id",
            "KandjiDevice",
            "id",
            "RESOURCE",
            rel_direction_right=True,
        )
        == expected_nodes_relationships
    )

    # Act: run the cleanup job to remove all nodes except the unrelated data
    common_job_parameters = {
        "UPDATE_TAG": UNRELATED_UPDATE_TAG,
        "TENANT_ID": TEST_KANDJI_TENANT_ID,
    }
    cartography.intel.kandji.devices.cleanup(
        neo4j_session,
        common_job_parameters,
    )

    # Assert: Expect unrelated data nodes remains
    expected_nodes_unrelated = {
        ("748C5E49-134E-486C-A609-88A66A1BE4A1",),
        ("706AF44A-9F51-4E84-B336-C4924097FFB6",),
    }

    assert (
        check_nodes(
            neo4j_session,
            "KandjiDevice",
            ["id"],
        )
        == expected_nodes_unrelated
    )

    # Cleanup test data
    common_job_parameters = {
        "UPDATE_TAG": TEST_UPDATE_TAG + 1234,
        "TENANT_ID": TEST_KANDJI_TENANT_ID,
    }
    cartography.intel.kandji.devices.cleanup(
        neo4j_session,
        common_job_parameters,
    )


def test_load_kandji_users_and_device_ownership(neo4j_session):
    # Arrange
    TEST_UPDATE_TAG = 1234
    TEST_KANDJI_TENANT_ID = tests.data.kandji.tenant.TENANT["simpson_corp"]["id"]
    common_job_parameters = {
        "UPDATE_TAG": TEST_UPDATE_TAG,
        "TENANT_ID": TEST_KANDJI_TENANT_ID,
    }
    users = cartography.intel.kandji.users.transform(
        tests.data.kandji.users.USERS["simpson_corp_users"],
    )

    # Act: users first, then devices (same order as start_kandji_ingestion)
    cartography.intel.kandji.users.load_users(
        neo4j_session,
        common_job_parameters,
        users,
    )
    cartography.intel.kandji.devices.load_devices(
        neo4j_session,
        common_job_parameters,
        tests.data.kandji.devices.DEVICES["simpson_corp_devices"],
    )

    # Assert: users exist and carry the UserAccount ontology label
    assert check_nodes(
        neo4j_session,
        "KandjiUser",
        ["id", "email", "integration_type"],
    ) == {
        (
            "0a1b2c3d-1111-4a4a-8888-000000000001",
            "hjsimpson@simpson.corp",
            "scim",
        ),
        (
            "0a1b2c3d-1111-4a4a-8888-000000000002",
            "mbsimpson@simpson.corp",
            "scim",
        ),
    }
    assert check_nodes(neo4j_session, "UserAccount", ["id"]) >= {
        ("0a1b2c3d-1111-4a4a-8888-000000000001",),
        ("0a1b2c3d-1111-4a4a-8888-000000000002",),
    }

    # Assert: users belong to the tenant
    assert check_rels(
        neo4j_session,
        "KandjiTenant",
        "id",
        "KandjiUser",
        "id",
        "RESOURCE",
        rel_direction_right=True,
    ) == {
        ("SimpsonCorp", "0a1b2c3d-1111-4a4a-8888-000000000001"),
        ("SimpsonCorp", "0a1b2c3d-1111-4a4a-8888-000000000002"),
    }

    # Assert: device health / enrollment fields are stored
    # Scoped to this tenant: earlier tests leave the other tenant's devices.
    devices = {
        tuple(r.values())
        for r in neo4j_session.run(
            """
            MATCH (:KandjiTenant {id: $tenant})-[:RESOURCE]->(d:KandjiDevice)
            RETURN d.id, d.is_missing, d.mdm_enabled, d.agent_installed, d.blueprint_name
            """,
            tenant=TEST_KANDJI_TENANT_ID,
        )
    }
    assert devices == {
        ("fc60decb-30cb-4db1-b3ec-2fa8ea1b83ed", False, True, True, "Standard Mac"),
        ("f27bcd08-f653-4930-83b0-51970e923b98", True, True, False, "Standard Mac"),
    }
    tags = neo4j_session.run(
        "MATCH (d:KandjiDevice {id: $id}) RETURN d.tags AS tags",
        id="fc60decb-30cb-4db1-b3ec-2fa8ea1b83ed",
    ).single()["tags"]
    assert tags == ["engineering"]

    # Assert: only the assigned device gets an OWNS edge
    assert check_rels(
        neo4j_session,
        "KandjiUser",
        "id",
        "KandjiDevice",
        "id",
        "OWNS",
        rel_direction_right=True,
    ) == {
        (
            "0a1b2c3d-1111-4a4a-8888-000000000001",
            "fc60decb-30cb-4db1-b3ec-2fa8ea1b83ed",
        ),
    }

    # Cleanup test data
    common_job_parameters = {
        "UPDATE_TAG": TEST_UPDATE_TAG + 1234,
        "TENANT_ID": TEST_KANDJI_TENANT_ID,
    }
    cartography.intel.kandji.devices.cleanup(neo4j_session, common_job_parameters)
    cartography.intel.kandji.users.cleanup(neo4j_session, common_job_parameters)


def test_cleanup_kandji_users(neo4j_session):
    # Arrange: stale users in one tenant, fresh users in another
    TEST_UPDATE_TAG = 1234
    SIMPSON = tests.data.kandji.tenant.TENANT["simpson_corp"]["id"]
    SOUTH_PARK = tests.data.kandji.tenant.TENANT["south_park"]["id"]
    cartography.intel.kandji.users.load_users(
        neo4j_session,
        {"UPDATE_TAG": TEST_UPDATE_TAG, "TENANT_ID": SIMPSON},
        cartography.intel.kandji.users.transform(
            tests.data.kandji.users.USERS["simpson_corp_users"],
        ),
    )
    cartography.intel.kandji.users.load_users(
        neo4j_session,
        {"UPDATE_TAG": TEST_UPDATE_TAG + 1, "TENANT_ID": SOUTH_PARK},
        cartography.intel.kandji.users.transform(
            tests.data.kandji.users.USERS["south_park_users"],
        ),
    )

    # Act: cleanup Simpson tenant with the newer tag
    cartography.intel.kandji.users.cleanup(
        neo4j_session,
        {"UPDATE_TAG": TEST_UPDATE_TAG + 1, "TENANT_ID": SIMPSON},
    )

    # Assert: only the other tenant's user remains
    assert check_nodes(neo4j_session, "KandjiUser", ["id"]) == {
        ("0a1b2c3d-2222-4a4a-8888-000000000001",),
    }

    # Cleanup test data
    cartography.intel.kandji.users.cleanup(
        neo4j_session,
        {"UPDATE_TAG": TEST_UPDATE_TAG + 1234, "TENANT_ID": SOUTH_PARK},
    )
