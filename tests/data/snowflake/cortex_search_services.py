"""Raw Snowflake Cortex Search service payloads.

Shaped as `GET /api/v2/databases/{db}/schemas/{schema}/cortex-search-services`
returns them: one service indexing a single table, and one built over a query,
which has no table to point at.

target_lag is deliberately the real nested-object shape Snowflake's object API
returns (confirmed against a real account, 2026-10-16:
`{"type": "USER_DEFINED", "seconds": N}`), not a pre-flattened string - a
previous version of this fixture used a plain string here, which hid a real
bug where cortex_search_services.py passed target_lag through to Neo4j
unflattened (Neo4j rejects a raw dict property outright). See
transform()'s schedule_to_text(service.get("target_lag")) call.
"""

from typing import Any

SNOWFLAKE_CORTEX_SEARCH_SERVICES: list[dict[str, Any]] = [
    {
        "name": "REACTOR_LOG_SEARCH",
        "target_lag": {"type": "USER_DEFINED", "seconds": 3600},
        "warehouse": "SECTOR_7G_WH",
        "source": "SPRINGFIELD.NUCLEAR_PLANT.REACTOR_READINGS",
        "embedding_model": "snowflake-arctic-embed-m-v1.5",
        "attribute_columns": ["READING_ID"],
        "search_column": "OPERATOR_NOTES",
        "service_query_url": (
            "https://springfield-nuclear.snowflakecomputing.com/api/v2/databases/"
            "SPRINGFIELD/schemas/NUCLEAR_PLANT/cortex-search-services/"
            "REACTOR_LOG_SEARCH:query"
        ),
        "comment": "Search what the operators wrote down",
        "created_on": "2026-08-03T17:20:00.000+00:00",
    },
    {
        "name": "SAFETY_MEMO_SEARCH",
        "target_lag": {"type": "USER_DEFINED", "seconds": 86400},
        "warehouse": None,
        "source": "SELECT MEMO_TEXT FROM SAFETY_MEMOS WHERE ARCHIVED = FALSE",
        "embedding_model": "snowflake-arctic-embed-m-v1.5",
        "attribute_columns": [],
        "search_column": "MEMO_TEXT",
        "service_query_url": None,
        "comment": None,
        "created_on": "2026-08-03T17:21:00.000+00:00",
    },
]
