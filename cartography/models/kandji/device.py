from dataclasses import dataclass

from cartography.models.core.common import PropertyRef
from cartography.models.core.nodes import CartographyNodeProperties
from cartography.models.core.nodes import CartographyNodeSchema
from cartography.models.core.relationships import CartographyRelProperties
from cartography.models.core.relationships import CartographyRelSchema
from cartography.models.core.relationships import LinkDirection
from cartography.models.core.relationships import make_target_node_matcher
from cartography.models.core.relationships import OtherRelationships
from cartography.models.core.relationships import TargetNodeMatcher


@dataclass(frozen=True)
class KandjiDeviceNodeProperties(CartographyNodeProperties):
    id: PropertyRef = PropertyRef("id", description="Kandji device ID.")
    lastupdated: PropertyRef = PropertyRef("lastupdated", set_in_kwargs=True)
    device_id: PropertyRef = PropertyRef(
        "device_id",
        description="Kandji device ID.",
    )
    device_name: PropertyRef = PropertyRef(
        "device_name",
        extra_index=True,
        description="Friendly device name.",
    )
    last_check_in: PropertyRef = PropertyRef(
        "last_check_in",
        description="Timestamp of the device's last Kandji check-in.",
    )
    model: PropertyRef = PropertyRef("model", description="Device model.")
    os_version: PropertyRef = PropertyRef(
        "os_version",
        description="Operating system version.",
    )
    platform: PropertyRef = PropertyRef(
        "platform",
        description="Device platform.",
    )
    serial_number: PropertyRef = PropertyRef(
        "serial_number",
        extra_index=True,
        description="Device serial number.",
    )
    udid: PropertyRef = PropertyRef(
        "udid",
        description="Apple unique device identifier.",
    )
    asset_tag: PropertyRef = PropertyRef(
        "asset_tag",
        description="Asset tag assigned in Kandji, if any.",
    )
    is_missing: PropertyRef = PropertyRef(
        "is_missing",
        description="True when the device has stopped checking in with Kandji.",
    )
    mdm_enabled: PropertyRef = PropertyRef(
        "mdm_enabled",
        description="Whether the device is enrolled in Kandji MDM.",
    )
    agent_installed: PropertyRef = PropertyRef(
        "agent_installed",
        description="Whether the Kandji agent is installed on the device.",
    )
    agent_version: PropertyRef = PropertyRef(
        "agent_version",
        description="Installed Kandji agent version.",
    )
    blueprint_id: PropertyRef = PropertyRef(
        "blueprint_id",
        description="ID of the Kandji blueprint (configuration profile set) assigned to the device.",
    )
    blueprint_name: PropertyRef = PropertyRef(
        "blueprint_name",
        description="Name of the Kandji blueprint assigned to the device.",
    )
    first_enrollment: PropertyRef = PropertyRef(
        "first_enrollment",
        description="Timestamp of the device's first Kandji enrollment.",
    )
    last_enrollment: PropertyRef = PropertyRef(
        "last_enrollment",
        description="Timestamp of the device's most recent Kandji enrollment.",
    )
    tags: PropertyRef = PropertyRef(
        "tags",
        description="Kandji tags applied to the device.",
    )


@dataclass(frozen=True)
class KandjiTenantToKandjiDeviceRelProperties(CartographyRelProperties):
    lastupdated: PropertyRef = PropertyRef("lastupdated", set_in_kwargs=True)


@dataclass(frozen=True)
# (:KandjiDevice)<-[:RESOURCE]-(:KandjiTenant)
class KandjiTenantToKandjiDeviceRel(CartographyRelSchema):
    """The tenant contains the enrolled device."""

    target_node_label: str = "KandjiTenant"
    target_node_matcher: TargetNodeMatcher = make_target_node_matcher(
        {"id": PropertyRef("TENANT_ID", set_in_kwargs=True)},
    )
    direction: LinkDirection = LinkDirection.INWARD
    rel_label: str = "RESOURCE"
    properties: KandjiTenantToKandjiDeviceRelProperties = (
        KandjiTenantToKandjiDeviceRelProperties()
    )


@dataclass(frozen=True)
# (:KandjiDevice)-[:ENROLLED_TO]->(:KandjiTenant) - Backwards compatibility
class KandjiDeviceToTenantDeprecatedRel(CartographyRelSchema):
    """Deprecated compatibility edge linking a device to its tenant."""

    target_node_label: str = "KandjiTenant"
    target_node_matcher: TargetNodeMatcher = make_target_node_matcher(
        {"id": PropertyRef("TENANT_ID", set_in_kwargs=True)},
    )
    direction: LinkDirection = LinkDirection.OUTWARD
    rel_label: str = "ENROLLED_TO"
    properties: KandjiTenantToKandjiDeviceRelProperties = (
        KandjiTenantToKandjiDeviceRelProperties()
    )


@dataclass(frozen=True)
class KandjiUserToKandjiDeviceRelProperties(CartographyRelProperties):
    lastupdated: PropertyRef = PropertyRef("lastupdated", set_in_kwargs=True)


@dataclass(frozen=True)
# (:KandjiUser)-[:OWNS]->(:KandjiDevice)
class KandjiUserToKandjiDeviceRel(CartographyRelSchema):
    """The Kandji user the device is assigned to."""

    target_node_label: str = "KandjiUser"
    target_node_matcher: TargetNodeMatcher = make_target_node_matcher(
        {"id": PropertyRef("user_id")},
    )
    direction: LinkDirection = LinkDirection.INWARD
    rel_label: str = "OWNS"
    properties: KandjiUserToKandjiDeviceRelProperties = (
        KandjiUserToKandjiDeviceRelProperties()
    )


@dataclass(frozen=True)
class KandjiDeviceSchema(CartographyNodeSchema):
    """A device managed by Kandji."""

    label: str = "KandjiDevice"  # The label of the node
    properties: KandjiDeviceNodeProperties = (
        KandjiDeviceNodeProperties()
    )  # An object representing all properties
    sub_resource_relationship: KandjiTenantToKandjiDeviceRel = (
        KandjiTenantToKandjiDeviceRel()
    )
    # DEPRECATED: for backward compatibility, will be removed in v1.0.0
    other_relationships: OtherRelationships = OtherRelationships(
        rels=[
            KandjiDeviceToTenantDeprecatedRel(),
            KandjiUserToKandjiDeviceRel(),
        ],
    )
