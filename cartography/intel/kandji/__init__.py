import logging

import neo4j

import cartography.intel.kandji.devices
import cartography.intel.kandji.users
from cartography.config import Config
from cartography.util import timeit

logger = logging.getLogger(__name__)


@timeit
def start_kandji_ingestion(neo4j_session: neo4j.Session, config: Config) -> None:
    """
    If this module is configured, perform ingestion of Kandji users and devices. Otherwise warn and exit

    :param neo4j_session: Neo4J session for database interface
    :param config: A cartography.config object

    :return: None
    """
    if (
        config.kandji_base_uri is None
        or config.kandji_token is None
        or config.kandji_tenant_id is None
    ):
        logger.warning(
            "Required parameter missing. Skipping sync. " "See docs to configure.",
        )
        return

    common_job_parameters = {
        "UPDATE_TAG": config.update_tag,
        "TENANT_ID": config.kandji_tenant_id,
    }

    # Users first: devices link to KandjiUser nodes via OWNS.
    cartography.intel.kandji.users.sync(
        neo4j_session,
        config.kandji_base_uri,
        config.kandji_token,
        common_job_parameters=common_job_parameters,
    )

    cartography.intel.kandji.devices.sync(
        neo4j_session,
        config.kandji_base_uri,
        config.kandji_token,
        common_job_parameters=common_job_parameters,
    )
