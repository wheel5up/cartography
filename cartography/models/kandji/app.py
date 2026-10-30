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
class KandjiTenantToKandjiAppRelProperties(CartographyRelProperties):
    lastupdated: PropertyRef = PropertyRef("lastupdated", set_in_kwargs=True)


# ---------------------------------------------------------------------------
# KandjiApp: one node per distinct application (bundle identifier)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class KandjiAppNodeProperties(CartographyNodeProperties):
    id: PropertyRef = PropertyRef(
        "id",
        description="Application bundle identifier (e.g. com.google.Chrome).",
    )
    lastupdated: PropertyRef = PropertyRef("lastupdated", set_in_kwargs=True)
    bundle_id: PropertyRef = PropertyRef(
        "bundle_id",
        extra_index=True,
        description="Application bundle identifier.",
    )
    name: PropertyRef = PropertyRef(
        "name",
        extra_index=True,
        description="Application display name.",
    )


@dataclass(frozen=True)
# (:KandjiTenant)-[:RESOURCE]->(:KandjiApp)
class KandjiTenantToKandjiAppRel(CartographyRelSchema):
    """The tenant contains the application."""

    target_node_label: str = "KandjiTenant"
    target_node_matcher: TargetNodeMatcher = make_target_node_matcher(
        {"id": PropertyRef("TENANT_ID", set_in_kwargs=True)},
    )
    direction: LinkDirection = LinkDirection.INWARD
    rel_label: str = "RESOURCE"
    properties: KandjiTenantToKandjiAppRelProperties = (
        KandjiTenantToKandjiAppRelProperties()
    )


@dataclass(frozen=True)
class KandjiAppSchema(CartographyNodeSchema):
    """An application observed installed on at least one Kandji-managed device."""

    label: str = "KandjiApp"
    properties: KandjiAppNodeProperties = KandjiAppNodeProperties()
    sub_resource_relationship: KandjiTenantToKandjiAppRel = KandjiTenantToKandjiAppRel()


# ---------------------------------------------------------------------------
# KandjiAppVersion: one node per distinct (bundle identifier, version)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class KandjiAppVersionNodeProperties(CartographyNodeProperties):
    id: PropertyRef = PropertyRef(
        "id",
        description="Bundle identifier and version, joined as '<bundle_id>:<version>'.",
    )
    lastupdated: PropertyRef = PropertyRef("lastupdated", set_in_kwargs=True)
    bundle_id: PropertyRef = PropertyRef(
        "bundle_id",
        extra_index=True,
        description="Application bundle identifier.",
    )
    name: PropertyRef = PropertyRef("name", description="Application display name.")
    version: PropertyRef = PropertyRef(
        "version",
        extra_index=True,
        description="Application version string as reported by Kandji.",
    )


@dataclass(frozen=True)
class KandjiTenantToKandjiAppVersionRelProperties(CartographyRelProperties):
    lastupdated: PropertyRef = PropertyRef("lastupdated", set_in_kwargs=True)


@dataclass(frozen=True)
# (:KandjiTenant)-[:RESOURCE]->(:KandjiAppVersion)
class KandjiTenantToKandjiAppVersionRel(CartographyRelSchema):
    """The tenant contains the application version."""

    target_node_label: str = "KandjiTenant"
    target_node_matcher: TargetNodeMatcher = make_target_node_matcher(
        {"id": PropertyRef("TENANT_ID", set_in_kwargs=True)},
    )
    direction: LinkDirection = LinkDirection.INWARD
    rel_label: str = "RESOURCE"
    properties: KandjiTenantToKandjiAppVersionRelProperties = (
        KandjiTenantToKandjiAppVersionRelProperties()
    )


@dataclass(frozen=True)
class KandjiAppVersionToKandjiAppRelProperties(CartographyRelProperties):
    lastupdated: PropertyRef = PropertyRef("lastupdated", set_in_kwargs=True)


@dataclass(frozen=True)
# (:KandjiAppVersion)-[:VERSION]->(:KandjiApp)
class KandjiAppVersionToKandjiAppRel(CartographyRelSchema):
    """Links a version to its application. Same shape as S1ApplicationVersion."""

    target_node_label: str = "KandjiApp"
    target_node_matcher: TargetNodeMatcher = make_target_node_matcher(
        {"id": PropertyRef("bundle_id")},
    )
    direction: LinkDirection = LinkDirection.OUTWARD
    rel_label: str = "VERSION"
    properties: KandjiAppVersionToKandjiAppRelProperties = (
        KandjiAppVersionToKandjiAppRelProperties()
    )


@dataclass(frozen=True)
class KandjiDeviceToKandjiAppVersionRelProperties(CartographyRelProperties):
    lastupdated: PropertyRef = PropertyRef("lastupdated", set_in_kwargs=True)
    path: PropertyRef = PropertyRef(
        "path",
        description="File system path of the installed application bundle.",
    )
    source: PropertyRef = PropertyRef(
        "source",
        description=(
            "Install source reported by Kandji, e.g. Apple, Identified Developer, "
            "Mac App Store, App Store - Other, Apple Business Manager. Empty when unknown."
        ),
    )
    signature: PropertyRef = PropertyRef(
        "signature",
        description="Code signing certificate chain, comma separated.",
    )
    modification_date: PropertyRef = PropertyRef(
        "modification_date",
        description="Last modification time of the installed bundle on disk.",
    )
    creation_date: PropertyRef = PropertyRef(
        "creation_date",
        description="Creation time of the installed bundle on disk.",
    )


@dataclass(frozen=True)
# (:KandjiDevice)-[:HAS_INSTALLED]->(:KandjiAppVersion)
class KandjiDeviceToKandjiAppVersionRel(CartographyRelSchema):
    """The device has this application version installed."""

    target_node_label: str = "KandjiDevice"
    target_node_matcher: TargetNodeMatcher = make_target_node_matcher(
        {"id": PropertyRef("device_id")},
    )
    direction: LinkDirection = LinkDirection.INWARD
    rel_label: str = "HAS_INSTALLED"
    properties: KandjiDeviceToKandjiAppVersionRelProperties = (
        KandjiDeviceToKandjiAppVersionRelProperties()
    )


@dataclass(frozen=True)
class KandjiAppVersionSchema(CartographyNodeSchema):
    """
    A specific application version installed on Kandji-managed devices.

    Loaded with one row per install (device, app); rows for the same version
    merge onto one node, each contributing its own HAS_INSTALLED edge.
    """

    label: str = "KandjiAppVersion"
    properties: KandjiAppVersionNodeProperties = KandjiAppVersionNodeProperties()
    sub_resource_relationship: KandjiTenantToKandjiAppVersionRel = (
        KandjiTenantToKandjiAppVersionRel()
    )
    other_relationships: OtherRelationships = OtherRelationships(
        rels=[
            KandjiAppVersionToKandjiAppRel(),
            KandjiDeviceToKandjiAppVersionRel(),
        ],
    )
