import json
from typing import Any

from mcp.server.fastmcp import FastMCP

from mcp_metricool.utils.utils import make_get_request
from mcp_metricool.utils.utils import make_post_request
from mcp_metricool.utils.utils import make_put_request
from mcp_metricool.utils.utils import network_subject_metrics
from mcp_metricool.config import METRICOOL_BASE_URL
from mcp_metricool.config import METRICOOL_USER_ID

# Initialize FastMCP server
mcp = FastMCP("metricool")

@mcp.tool()
async def get_brands(state: str) -> str | dict[str, Any]:
    """
    Get the list of brands from your Metricool account.
    Add to the result that the only networks with competitors are Instagram, Facebook, Twitch, YouTube, Twitter, and Bluesky.
    """

    url = f"{METRICOOL_BASE_URL}/v2/settings/brands?userId={METRICOOL_USER_ID}&integrationSource=MCP"

    response = await make_get_request(url)

    if not response:
        return ("Failed to get brands")

    return {
    "brands": response,
    "instructions": (
        "Explain that only Instagram, Facebook, Twitch, YouTube, Twitter, and Bluesky support competitors. "
    )
}



