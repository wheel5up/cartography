from dataclasses import dataclass

from cartography.models.core.common import PropertyRef
from cartography.models.core.nodes import CartographyNodeProperties
from cartography.models.core.nodes import CartographyNodeSchema
from cartography.models.core.nodes import ExtraNodeLabels
from cartography.models.core.relationships import CartographyRelProperties
from cartography.models.core.relationships import CartographyRelSchema
from cartography.models.core.relationships import LinkDirection
from cartography.models.core.relationships import make_target_node_matcher
from cartography.models.core.relationships import TargetNodeMatcher
from cartography.models.ontology.labels import USER_ACCOUNT


@dataclass(frozen=True)
class KandjiUserNodeProperties(CartographyNodeProperties):
    id: PropertyRef = PropertyRef("id", description="Kandji user ID.")
    lastupdated: PropertyRef = PropertyRef("lastupdated", set_in_kwargs=True)
    email: PropertyRef = PropertyRef(
        "email",
        extra_index=True,
        description="User email address.",
    )
    name: PropertyRef = PropertyRef("name", description="User display name.")
    active: PropertyRef = PropertyRef(
        "active",
        description="Whether the user is active in Kandji.",
    )
    archived: PropertyRef = PropertyRef(
        "archived",
        description="Whether the user has been archived in Kandji.",
    )
    department: PropertyRef = PropertyRef(
        "department",
        description="Department reported by the user directory integration.",
    )
    job_title: PropertyRef = PropertyRef(
        "job_title",
        description="Job title reported by the user directory integration.",
    )
    device_count: PropertyRef = PropertyRef(
        "device_count",
        description="Number of Kandji devices assigned to the user.",
    )
    integration_type: PropertyRef = PropertyRef(
        "integration_type",
        description="Type of directory integration that provisioned the user (e.g. scim).",
    )
    created_at: PropertyRef = PropertyRef(
        "created_at",
        description="Timestamp when the user was created in Kandji.",
    )
    updated_at: PropertyRef = PropertyRef(
        "updated_at",
        description="Timestamp when the user was last updated in Kandji.",
    )


@dataclass(frozen=True)
class KandjiTenantToKandjiUserRelProperties(CartographyRelProperties):
    lastupdated: PropertyRef = PropertyRef("lastupdated", set_in_kwargs=True)


@dataclass(frozen=True)
# (:KandjiTenant)-[:RESOURCE]->(:KandjiUser)
class KandjiTenantToKandjiUserRel(CartographyRelSchema):
    """The tenant contains the user."""

    target_node_label: str = "KandjiTenant"
    target_node_matcher: TargetNodeMatcher = make_target_node_matcher(
        {"id": PropertyRef("TENANT_ID", set_in_kwargs=True)},
    )
    direction: LinkDirection = LinkDirection.INWARD
    rel_label: str = "RESOURCE"
    properties: KandjiTenantToKandjiUserRelProperties = (
        KandjiTenantToKandjiUserRelProperties()
    )


@dataclass(frozen=True)
class KandjiUserSchema(CartographyNodeSchema):
    """A user in the Kandji user directory, typically provisioned via SCIM."""

    label: str = "KandjiUser"
    extra_node_labels: ExtraNodeLabels = ExtraNodeLabels(
        [USER_ACCOUNT]
    )  # UserAccount label is used for ontology mapping
    properties: KandjiUserNodeProperties = KandjiUserNodeProperties()
    sub_resource_relationship: KandjiTenantToKandjiUserRel = (
        KandjiTenantToKandjiUserRel()
    )
