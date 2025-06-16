#!/usr/bin/env python3
"""
MCP Proxy Client - Connects Claude Desktop to SSE server
Acts as a bridge between stdio (Claude Desktop) and HTTP SSE server
"""

import asyncio
import json
import sys
import aiohttp
import logging
from typing import Dict, Any

class MCPProxyClient:
    """Proxy client that bridges stdio and SSE server"""
    
    def __init__(self, server_url: str):
        self.server_url = server_url
        self.session = None
        self.logger = logging.getLogger(__name__)
    
    async def start(self):
        """Start the proxy client"""
        self.session = aiohttp.ClientSession()
        
        # Start reading from stdin and sending to server
        await self.run_proxy()
    
    async def run_proxy(self):
        """Main proxy loop"""
        try:
            while True:
                # Read JSON-RPC message from stdin
                line = await asyncio.get_event_loop().run_in_executor(
                    None, sys.stdin.readline
                )
                
                if not line:
                    break
                
                try:
                    # Parse the incoming message
                    message = json.loads(line.strip())
                    
                    # Send to SSE server and get response
                    response = await self.send_to_server(message)
                    
                    # Send response back to Claude Desktop via stdout
                    print(json.dumps(response), flush=True)
                    
                except json.JSONDecodeError as e:
                    self.logger.error(f"Invalid JSON from stdin: {e}")
                    error_response = {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {
                            "code": -32700,
                            "message": "Parse error"
                        }
                    }
                    print(json.dumps(error_response), flush=True)
                
        except Exception as e:
            self.logger.error(f"Proxy error: {e}")
        finally:
            if self.session:
                await self.session.close()
    
    async def send_to_server(self, message: Dict[str, Any]) -> Dict[str, Any]:
        """Send message to SSE server and return response"""
        try:
            async with self.session.post(
                f"{self.server_url}/sse",
                json=message,
                headers={"Content-Type": "application/json"}
            ) as response:
                
                if response.status == 200:
                    return await response.json()
                else:
                    error_text = await response.text()
                    return {
                        "jsonrpc": "2.0",
                        "id": message.get("id"),
                        "error": {
                            "code": -32603,
                            "message": f"Server error: {response.status} - {error_text}"
                        }
                    }
                    
        except Exception as e:
            return {
                "jsonrpc": "2.0",
                "id": message.get("id"),
                "error": {
                    "code": -32603,
                    "message": f"Connection error: {str(e)}"
                }
            }


async def main():
    """Main function"""
    import os
    
    # Get server URL from environment or use default
    server_url = os.getenv("MCP_SERVER_URL", "http://localhost:8000")
    
    # Setup logging to stderr (stdout is used for JSON-RPC)
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    logger = logging.getLogger(__name__)
    logger.info(f"Starting MCP proxy client, connecting to: {server_url}")
    
    # Create and start proxy client
    proxy = MCPProxyClient(server_url)
    await proxy.start()


if __name__ == "__main__":
    asyncio.run(main())
