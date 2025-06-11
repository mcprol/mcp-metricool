#!/usr/bin/env python3
"""
MCP SSE Server que reutiliza las herramientas definidas en tools.py
"""

import asyncio
import json
import logging
import argparse
from typing import Any, Dict, List, Optional, Sequence
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.responses import StreamingResponse
from sse_starlette import EventSourceResponse

from mcp import McpServer
from mcp.server.sse import SseServerTransport
from mcp.types import (
    CallToolRequest,
    ListToolsRequest,
    TextContent,
    Tool,
    EmbeddedResource,
    LoggingLevel
)

# Importar las herramientas desde tu archivo tools.py existente
from tools import (
    get_tools,  # Función que devuelve la lista de herramientas
    call_tool,  # Función que ejecuta una herramienta específica
    # Si tu estructura es diferente, ajusta estos imports
)

# Configurar logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class McpSseServer:
    def __init__(self, host: str = "0.0.0.0", port: int = 8080):
        self.host = host
        self.port = port
        self.server = McpServer("metricool-sse-server")
        self.setup_handlers()
        
    def setup_handlers(self):
        """Configura los manejadores del servidor MCP"""
        
        @self.server.list_tools()
        async def handle_list_tools() -> List[Tool]:
            """Obtiene la lista de herramientas desde tools.py"""
            try:
                # Llamar a tu función get_tools() existente
                tools_data = get_tools()
                
                # Convertir a formato MCP Tool si es necesario
                mcp_tools = []
                for tool_data in tools_data:
                    mcp_tool = Tool(
                        name=tool_data.get("name", ""),
                        description=tool_data.get("description", ""),
                        inputSchema=tool_data.get("inputSchema", {})
                    )
                    mcp_tools.append(mcp_tool)
                
                logger.info(f"Returning {len(mcp_tools)} tools")
                return mcp_tools
                
            except Exception as e:
                logger.error(f"Error listing tools: {e}")
                return []

        @self.server.call_tool()
        async def handle_call_tool(name: str, arguments: Dict[str, Any]) -> Sequence[TextContent]:
            """Ejecuta una herramienta usando la función call_tool existente"""
            try:
                logger.info(f"Calling tool: {name} with arguments: {arguments}")
                
                # Llamar a tu función call_tool() existente
                result = await call_tool(name, arguments)
                
                # Convertir el resultado a formato MCP
                if isinstance(result, str):
                    content = [TextContent(type="text", text=result)]
                elif isinstance(result, dict):
                    content = [TextContent(type="text", text=json.dumps(result, indent=2))]
                elif isinstance(result, list):
                    content = [TextContent(type="text", text=json.dumps(result, indent=2))]
                else:
                    content = [TextContent(type="text", text=str(result))]
                
                logger.info(f"Tool {name} executed successfully")
                return content
                
            except Exception as e:
                error_msg = f"Error executing tool {name}: {str(e)}"
                logger.error(error_msg)
                return [TextContent(type="text", text=error_msg)]

    def create_app(self) -> FastAPI:
        """Crea la aplicación FastAPI con SSE"""
        
        @asynccontextmanager
        async def lifespan(app: FastAPI):
            logger.info("Starting MCP SSE Server")
            yield
            logger.info("Shutting down MCP SSE Server")
        
        app = FastAPI(
            title="Metricool MCP SSE Server",
            description="MCP Server via SSE que reutiliza tools.py",
            version="1.0.0",
            lifespan=lifespan
        )
        
        @app.get("/health")
        async def health_check():
            return {"status": "healthy", "server": "metricool-mcp-sse"}
        
        @app.post("/sse")
        async def handle_sse(request: Request):
            """Maneja las conexiones SSE para MCP"""
            
            async def event_publisher():
                # Crear el transporte SSE
                transport = SseServerTransport("/sse")
                
                # Configurar el servidor con el transporte
                async with transport.connect_sse(
                    request,
                    self.server,
                    {"metricool-mcp-sse": "1.0.0"}
                ) as connection:
                    # Mantener la conexión viva
                    try:
                        await connection.run()
                    except Exception as e:
                        logger.error(f"SSE connection error: {e}")
                        raise
            
            return EventSourceResponse(event_publisher())
        
        return app

    async def run(self):
        """Ejecuta el servidor"""
        app = self.create_app()
        config = uvicorn.Config(
            app=app,
            host=self.host,
            port=self.port,
            log_level="info"
        )
        server = uvicorn.Server(config)
        
        logger.info(f"Starting MCP SSE Server on {self.host}:{self.port}")
        logger.info(f"SSE endpoint: http://{self.host}:{self.port}/sse")
        logger.info(f"Health check: http://{self.host}:{self.port}/health")
        
        await server.serve()

# Versión alternativa si tu tools.py tiene una estructura diferente
class McpSseServerAlternative:
    """
    Versión alternativa para cuando tools.py tiene una estructura diferente
    """
    
    def __init__(self, host: str = "0.0.0.0", port: int = 8080):
        self.host = host
        self.port = port
        self.server = McpServer("metricool-sse-server")
        self.setup_handlers()
        
        # Importar dinámicamente las herramientas
        self.load_tools_from_module()
        
    def load_tools_from_module(self):
        """Carga las herramientas desde el módulo tools.py dinámicamente"""
        import importlib
        import inspect
        
        # Importar el módulo tools
        tools_module = importlib.import_module('tools')
        
        # Obtener todas las funciones que empiecen con 'tool_' o tengan decorador @tool
        self.available_tools = {}
        for name, obj in inspect.getmembers(tools_module):
            if inspect.isfunction(obj) and (
                name.startswith('tool_') or 
                hasattr(obj, '_is_mcp_tool')  # Si usas decoradores
            ):
                self.available_tools[name] = obj
                logger.info(f"Loaded tool: {name}")
    
    def setup_handlers(self):
        """Configura los manejadores basándose en las herramientas cargadas"""
        
        @self.server.list_tools()
        async def handle_list_tools() -> List[Tool]:
            tools = []
            for tool_name, tool_func in self.available_tools.items():
                # Extraer metadatos de la función
                doc = tool_func.__doc__ or f"Tool: {tool_name}"
                
                # Intentar obtener el schema de los parámetros
                sig = inspect.signature(tool_func)
                input_schema = {
                    "type": "object",
                    "properties": {},
                    "required": []
                }
                
                for param_name, param in sig.parameters.items():
                    if param_name != 'self':  # Excluir self si es un método
                        input_schema["properties"][param_name] = {
                            "type": "string",  # Tipo por defecto
                            "description": f"Parameter {param_name}"
                        }
                        if param.default == inspect.Parameter.empty:
                            input_schema["required"].append(param_name)
                
                tool = Tool(
                    name=tool_name,
                    description=doc,
                    inputSchema=input_schema
                )
                tools.append(tool)
            
            return tools
        
        @self.server.call_tool()
        async def handle_call_tool(name: str, arguments: Dict[str, Any]) -> Sequence[TextContent]:
            if name not in self.available_tools:
                return [TextContent(type="text", text=f"Tool {name} not found")]
            
            try:
                tool_func = self.available_tools[name]
                
                # Llamar a la función con los argumentos
                if asyncio.iscoroutinefunction(tool_func):
                    result = await tool_func(**arguments)
                else:
                    result = tool_func(**arguments)
                
                # Convertir resultado a texto
                if isinstance(result, (dict, list)):
                    text_result = json.dumps(result, indent=2)
                else:
                    text_result = str(result)
                
                return [TextContent(type="text", text=text_result)]
                
            except Exception as e:
                error_msg = f"Error executing {name}: {str(e)}"
                logger.error(error_msg)
                return [TextContent(type="text", text=error_msg)]

def main():
    """Función principal"""
    parser = argparse.ArgumentParser(description="MCP SSE Server para Metricool")
    parser.add_argument("--host", default="0.0.0.0", help="Host del servidor")
    parser.add_argument("--port", type=int, default=8080, help="Puerto del servidor")
    parser.add_argument("--alternative", action="store_true", 
                       help="Usar el servidor alternativo para estructuras diferentes")
    
    args = parser.parse_args()
    
    # Seleccionar el tipo de servidor basándose en los argumentos
    if args.alternative:
        server = McpSseServerAlternative(host=args.host, port=args.port)
    else:
        server = McpSseServer(host=args.host, port=args.port)
    
    # Ejecutar el servidor
    try:
        asyncio.run(server.run())
    except KeyboardInterrupt:
        logger.info("Servidor detenido por el usuario")
    except Exception as e:
        logger.error(f"Error ejecutando el servidor: {e}")

if __name__ == "__main__":
    main()
