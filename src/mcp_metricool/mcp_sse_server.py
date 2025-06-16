#!/usr/bin/env python3
"""
MCP SSE Server que reutiliza las herramientas definidas en tools.py con FastMCP
"""

import asyncio
import json
import logging
import argparse
import inspect
from typing import Any, Dict, List, Optional, Sequence
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from sse_starlette import EventSourceResponse

from mcp.server import Server
from mcp.server.sse import SseServerTransport
from mcp.types import (
    CallToolRequest,
    ListToolsRequest,
    TextContent,
    Tool,
    EmbeddedResource,
    LoggingLevel
)

# Importar el objeto FastMCP desde tu archivo tools.py
from mcp_metricool.tools.tools import mcp as fastmcp_instance

# Configurar logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)





# ========== FUNCIONES DE VALIDACIÓN JSON ==========
def validate_json_content(body: bytes) -> tuple[bool, Any]:
    """Valida que el contenido sea JSON válido"""
    if not body:
        return True, None  # Body vacío es válido para algunos casos
    
    try:
        decoded = body.decode('utf-8')
        if not decoded.strip():
            return True, None
        
        data = json.loads(decoded)
        return True, data
    except UnicodeDecodeError as e:
        logger.error(f"Error decodificando UTF-8: {e}")
        return False, {"error": {"code": -32700, "message": "Invalid UTF-8 encoding"}}
    except json.JSONDecodeError as e:
        logger.error(f"Error decodificando JSON: {e}")
        return False, {"error": {"code": -32700, "message": f"Invalid JSON: {str(e)}"}}
    except Exception as e:
        logger.error(f"Error inesperado validando JSON: {e}")
        return False, {"error": {"code": -32700, "message": f"Validation error: {str(e)}"}}

def validate_jsonrpc_structure(data: Any) -> tuple[bool, Optional[Dict]]:
    """Valida estructura JSON-RPC 2.0 si aplica"""
    if data is None:
        return True, None
    
    if not isinstance(data, dict):
        return False, {"error": {"code": -32600, "message": "Request must be JSON object"}}
    
    # Si tiene campos JSON-RPC, validarlos
    if "jsonrpc" in data:
        if data.get("jsonrpc") != "2.0":
            return False, {"error": {"code": -32600, "message": f"jsonrpc must be '2.0': {str(data)}"}}
        
        if "method" not in data:
            return False, {"error": {"code": -32600, "message": f"Missing 'method' field: {str(data)}"}}
        
    return True, None

def create_json_error_response(error_data: Dict, request_id=None) -> Dict:
    """Crea respuesta de error JSON-RPC"""
    return {
        "jsonrpc": "2.0",
        "error": error_data["error"],
        "id": request_id
    }

async def safe_read_and_validate_json(request: Request) -> tuple[bool, Any, Optional[Dict]]:
    """Lee y valida JSON de forma segura"""
    try:
        body = await request.body()
        logger.info(f"Received body (first 200 chars): {str(body)[:200]}")
        
        # Validar JSON básico
        is_valid_json, json_data_or_error = validate_json_content(body)
        if not is_valid_json:
            return False, None, json_data_or_error
        
        # Validar estructura JSON-RPC si aplica
        is_valid_rpc, rpc_error = validate_jsonrpc_structure(json_data_or_error)
        if not is_valid_rpc:
            #mcprol return False, None, rpc_error
            return True, None, None
        
        return True, json_data_or_error, None
        
    except Exception as e:
        logger.error(f"Error leyendo request: {e}")
        return False, None, {"error": {"code": -32700, "message": f"Request processing error: {str(e)}"}}




class McpSseServer:
    def __init__(self, host: str = "0.0.0.0", port: int = 8080):
        self.host = host
        self.port = port
        self.server = Server("metricool-sse-server")
        self.fastmcp_instance = fastmcp_instance
        self.fastmcp_app = None  # Se inicializará en _initialize_server
        self.app: Optional[FastAPI] = None
        self.setup_handlers()
        self._initialized = False
        self.first_get = True
       
    def _initialize_server(self):
        """Inicializar el servidor MCP y la aplicación FastAPI"""
        if self._initialized:
            return
            
        try:
            # IMPORTANTE: Crear la aplicación streamable ANTES de acceder al session manager
            # Esto inicializa internamente el session manager
            self.fastmcp_app = self.fastmcp_instance.streamable_http_app()
            
            logger.info("Servidor MCP inicializado correctamente")
            logger.debug(f"Session manager disponible: {hasattr(self.fastmcp_instance, 'session_manager')}")
            
            self._initialized = True
            
        except Exception as e:
            logger.error(f"Error inicializando el servidor: {e}")
            raise
            
    async def extract_tools_from_fastmcp(self):
        """Extrae las herramientas registradas en FastMCP"""
        tools = []
        
        # Asegurarse de que FastMCP esté inicializado
        if not self._initialized:
            self._initialize_server()
        
        logger.info("Extrayendo herramientas registradas en FastMCP")

        # Método principal: usar list_tools si está disponible
        if hasattr(self.fastmcp_instance, 'list_tools'):
            try:
                logger.info("Usando list_tools() de FastMCP")
                registered_tools = await self.fastmcp_instance.list_tools()
                
                # Si registered_tools es una lista de herramientas Tool
                if isinstance(registered_tools, list):
                    return registered_tools
                
                # Si es un diccionario o tiene otro formato
                logger.info(f"Herramientas registradas: {registered_tools}")
                
            except Exception as e:
                logger.error(f"Error al obtener herramientas con list_tools(): {e}")

        # Método alternativo: buscar en _tools
        if hasattr(self.fastmcp_instance, '_tools'):
            logger.info("Extrayendo herramientas desde _tools")
            for tool_name, tool_info in self.fastmcp_instance._tools.items():
                try:
                    # Obtener información de la herramienta
                    func = tool_info.get('func') or tool_info.get('handler')
                    description = tool_info.get('description', '')
                    
                    if not description and func:
                        description = func.__doc__ or f"Tool: {tool_name}"
                    
                    # Generar schema de entrada basado en la función
                    input_schema = self.generate_input_schema(func)
                    
                    tool = Tool(
                        name=tool_name,
                        description=description,
                        inputSchema=input_schema
                    )
                    tools.append(tool)
                    logger.info(f"Herramienta extraída: {tool_name}")
                except Exception as e:
                    logger.error(f"Error procesando herramienta {tool_name}: {e}")
        
        return tools
    
    def generate_input_schema(self, func):
        """Genera el schema de entrada basado en la signatura de la función"""
        if not func:
            return {"type": "object", "properties": {}, "required": []}
        
        try:
            sig = inspect.signature(func)
            schema = {
                "type": "object",
                "properties": {},
                "required": []
            }
            
            for param_name, param in sig.parameters.items():
                if param_name in ['self', 'cls']:
                    continue
                
                # Determinar el tipo basado en la anotación
                param_type = "string"  # tipo por defecto
                if param.annotation != inspect.Parameter.empty:
                    if param.annotation in [str, Optional[str]]:
                        param_type = "string"
                    elif param.annotation in [int, Optional[int]]:
                        param_type = "integer"
                    elif param.annotation in [bool, Optional[bool]]:
                        param_type = "boolean"
                    elif param.annotation in [dict, Dict]:
                        param_type = "object"
                    elif param.annotation in [list, List]:
                        param_type = "array"
                
                schema["properties"][param_name] = {
                    "type": param_type,
                    "description": f"Parameter {param_name}"
                }
                
                # Añadir a required si no tiene valor por defecto
                if param.default == inspect.Parameter.empty:
                    schema["required"].append(param_name)
            
            return schema
        except Exception as e:
            logger.warning(f"Error generating schema for {func.__name__}: {e}")
            return {"type": "object", "properties": {}, "required": []}
        
    def setup_handlers(self):
        """Configura los manejadores del servidor MCP"""
        
        @self.server.list_tools()
        async def handle_list_tools() -> List[Tool]:
            """Obtiene la lista de herramientas desde FastMCP"""
            try:
                tools = await self.extract_tools_from_fastmcp()
                logger.info(f"Returning {len(tools)} tools")
                return tools
                
            except Exception as e:
                logger.error(f"Error listing tools: {e}")
                return []

        @self.server.call_tool()
        async def handle_call_tool(name: str, arguments: Dict[str, Any]) -> Sequence[TextContent]:
            """Ejecuta una herramienta usando FastMCP"""
            try:
                # Asegurarse de que FastMCP esté inicializado
                if not self._initialized:
                    self._initialize_server()
                
                logger.info(f"Calling tool: {name} with arguments: {arguments}")
                
                # Buscar la herramienta en FastMCP
                tool_func = None
                
                # Método 1: Buscar en el registro de herramientas
                if hasattr(self.fastmcp_instance, '_tools') and name in self.fastmcp_instance._tools:
                    tool_info = self.fastmcp_instance._tools[name]
                    tool_func = tool_info.get('func') or tool_info.get('handler')
                
                # Método 2: Buscar por atributo
                elif hasattr(self.fastmcp_instance, name):
                    potential_func = getattr(self.fastmcp_instance, name)
                    if callable(potential_func):
                        tool_func = potential_func
                
                if not tool_func:
                    error_msg = f"Tool {name} not found"
                    logger.error(error_msg)
                    return [TextContent(type="text", text=error_msg)]
                
                # Ejecutar la herramienta
                if asyncio.iscoroutinefunction(tool_func):
                    result = await tool_func(**arguments)
                else:
                    result = tool_func(**arguments)
                
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
            # Inicializar FastMCP si tiene método de inicialización
            if hasattr(self.fastmcp_instance, 'initialize'):
                try:
                    await self.fastmcp_instance.initialize()
                except Exception as e:
                    logger.error(f"Error inicializando FastMCP: {e}")
            yield
            logger.info("Shutting down MCP SSE Server")
        
        app = FastAPI(
            title="Metricool MCP SSE Server",
            description="MCP Server via SSE que reutiliza FastMCP tools",
            version="1.0.0",
            lifespan=lifespan
        )
        
        # Agregar CORS middleware para permitir conexiones desde el navegador
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
        
        @app.get("/health")
        async def health_check():
            try:
                tools_count = len(await self.extract_tools_from_fastmcp())
                return {
                    "status": "healthy", 
                    "server": "metricool-mcp-sse",
                    "tools_available": tools_count
                }
            except Exception as e:
                logger.error(f"Error en health check: {e}")
                return {
                    "status": "error",
                    "server": "metricool-mcp-sse",
                    "error": str(e)
                }
        
        @app.get("/tools")
        async def list_available_tools():
            """Endpoint para listar herramientas disponibles (debug)"""
            try:
                tools = await self.extract_tools_from_fastmcp()
                return {
                    "tools": [
                        {
                            "name": tool.name,
                            "description": tool.description,
                            "input_schema": tool.inputSchema
                        }
                        for tool in tools
                    ]
                }
            except Exception as e:
                logger.error(f"Error listando herramientas: {e}")
                return {"error": str(e), "tools": []}
        
        # ========== NUEVO ENDPOINT /events PARA RAYCAST ==========
        @app.get("/events")
        async def handle_events():
            """Endpoint /events específico para Raycast - Stream SSE básico"""
            
            if not self._initialized:
                self._initialize_server()
                
            logger.info("Raycast conectado al endpoint /events")
            
            async def events_stream():
                try:
                    # Mensaje de conexión inicial
                    yield f"data: {json.dumps({'type': 'connected', 'message': 'MCP SSE Server connected', 'timestamp': str(asyncio.get_event_loop().time())})}\n\n"
                    
                    # Enviar lista de herramientas disponibles
                    tools = await self.extract_tools_from_fastmcp()
                    tools_data = [
                        {
                            'name': tool.name, 
                            'description': tool.description,
                            'inputSchema': tool.inputSchema
                        } 
                        for tool in tools
                    ]
                    yield f"data: {json.dumps({'type': 'tools_list', 'tools': tools_data})}\n\n"
                    
                    # Mantener la conexión viva con heartbeat
                    counter = 0
                    while True:
                        counter += 1
                        heartbeat_data = {
                            'type': 'heartbeat',
                            'counter': counter,
                            'timestamp': str(asyncio.get_event_loop().time()),
                            'server_status': 'running'
                        }
                        yield f"data: {json.dumps(heartbeat_data)}\n\n"
                        await asyncio.sleep(30)  # Heartbeat cada 30 segundos
                        
                except asyncio.CancelledError:
                    logger.info("Conexión /events cancelada por el cliente")
                    return
                except Exception as e:
                    logger.error(f"Error en /events stream: {e}")
                    yield f"data: {json.dumps({'type': 'error', 'error': str(e)})}\n\n"
            
            return EventSourceResponse(
                events_stream(),
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "Content-Type": "text/event-stream",
                    "Access-Control-Allow-Origin": "*",
                    "Access-Control-Allow-Headers": "*",
                    "Access-Control-Allow-Methods": "GET, OPTIONS",
                }
            )
        
        @app.options("/events")
        async def events_options():
            """Maneja las solicitudes OPTIONS para /events (CORS preflight)"""
            return Response(
                headers={
                    "Access-Control-Allow-Origin": "*",
                    "Access-Control-Allow-Methods": "GET, OPTIONS",
                    "Access-Control-Allow-Headers": "*",
                    "Access-Control-Max-Age": "86400",
                }
            )
        
        # ========== ENDPOINT /events CON POST PARA INTERACCIÓN ==========
        @app.post("/events")
        async def handle_events_post(request: Request):
            """Endpoint POST /events para manejar comandos de Raycast"""
            
            if not self._initialized:
                self._initialize_server()
                
            logger.info("Recibida petición POST en /events")
            
            async def events_post_stream():
                try:
                    # Leer el cuerpo de la petición
                    body = await request.body()
                    logger.info(f"Body recibido: {body}")
                    
                    # Procesar el comando si viene en JSON
                    command_data = None
                    if body:
                        try:
                            command_data = json.loads(body.decode())
                            logger.info(f"Comando procesado: {command_data}")
                        except json.JSONDecodeError:
                            logger.warning("No se pudo parsear el body como JSON")
                    
                    # Mensaje de confirmación
                    yield f"data: {json.dumps({'type': 'command_received', 'command': command_data})}\n\n"
                    
                    # Si hay un comando específico, procesarlo
                    if command_data and 'action' in command_data:
                        action = command_data['action']
                        
                        if action == 'list_tools':
                            tools = await self.extract_tools_from_fastmcp()
                            tools_data = [{'name': t.name, 'description': t.description} for t in tools]
                            yield f"data: {json.dumps({'type': 'tools_response', 'tools': tools_data})}\n\n"
                        
                        elif action == 'call_tool':
                            tool_name = command_data.get('tool_name')
                            tool_args = command_data.get('arguments', {})
                            
                            if tool_name:
                                # Ejecutar la herramienta
                                result = await self.handle_call_tool(tool_name, tool_args)
                                result_text = result[0].text if result else "No result"
                                yield f"data: {json.dumps({'type': 'tool_result', 'tool': tool_name, 'result': result_text})}\n\n"
                    
                    # Mantener conexión abierta para más comandos
                    yield f"data: {json.dumps({'type': 'ready', 'message': 'Ready for more commands'})}\n\n"
                    
                    # Mantener viva la conexión
                    counter = 0
                    while True:
                        counter += 1
                        yield f"data: {json.dumps({'type': 'heartbeat', 'counter': counter})}\n\n"
                        await asyncio.sleep(30)
                        
                except asyncio.CancelledError:
                    logger.info("Conexión POST /events cancelada")
                    return
                except Exception as e:
                    logger.error(f"Error en POST /events: {e}")
                    yield f"data: {json.dumps({'type': 'error', 'error': str(e)})}\n\n"
            
            return EventSourceResponse(
                events_post_stream(),
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "Content-Type": "text/event-stream",
                    "Access-Control-Allow-Origin": "*",
                    "Access-Control-Allow-Headers": "*",
                }
            )
        
        @app.post("/sse")
        async def handle_sse(request: Request):
            """Maneja las conexiones SSE para MCP - Implementación simplificada"""
            
            # Asegurar que FastMCP esté inicializado
            if not self._initialized:
                self._initialize_server()
            
            logger.info("Iniciando conexión SSE desde Raycast")
            
            async def sse_generator():
                try:
                    # Crear el transporte SSE correctamente
                    transport = SseServerTransport("/sse")
                    
                    # Función para manejar el envío de respuestas
                    response_queue = asyncio.Queue()
                    
                    async def custom_send(message):
                        """Función de envío personalizada para SSE"""
                        await response_queue.put(message)
                    
                    # Función para recibir datos del request
                    async def custom_receive():
                        """Función de recepción personalizada"""
                        body = await request.body()
                        return {
                            'type': 'http.request',
                            'body': body,
                            'more_body': False
                        }
                    
                    # Crear la conexión SSE
                    try:
                        # Intentar con el método más directo
                        async with transport.connect_sse(
                            scope=request.scope,
                            receive=custom_receive,
                            send=custom_send,
                            server=self.server
                        ) as connection:
                            # Ejecutar en paralelo: connection.run() y el procesamiento de mensajes
                            async def run_connection():
                                try:
                                    await connection.run()
                                except Exception as e:
                                    logger.error(f"Error en connection.run(): {e}")
                                    await response_queue.put({'type': 'error', 'error': str(e)})
                            
                            # Iniciar la conexión en background
                            connection_task = asyncio.create_task(run_connection())
                            
                            # Procesar mensajes del queue
                            while not connection_task.done():
                                try:
                                    # Esperar mensaje con timeout
                                    message = await asyncio.wait_for(
                                        response_queue.get(), 
                                        timeout=1.0
                                    )
                                    
                                    if message.get('type') == 'error':
                                        yield f"event: error\ndata: {json.dumps({'error': message['error']})}\n\n"
                                        break
                                    elif message.get('type') == 'http.response.body':
                                        body = message.get('body', b'')
                                        if body:
                                            yield body.decode() if isinstance(body, bytes) else str(body)
                                    
                                except asyncio.TimeoutError:
                                    # Enviar keepalive
                                    yield ": keepalive\n\n"
                                    continue
                                except Exception as e:
                                    logger.error(f"Error procesando mensaje: {e}")
                                    yield f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"
                                    break
                    
                    except Exception as e:
                        logger.error(f"Error creando conexión SSE: {e}")
                        import traceback
                        logger.error(f"Traceback: {traceback.format_exc()}")
                        yield f"event: error\ndata: {json.dumps({'error': f'Connection error: {str(e)}'})}\n\n"
                
                except Exception as e:
                    logger.error(f"Error fatal en SSE generator: {e}")
                    import traceback
                    logger.error(f"Traceback completo: {traceback.format_exc()}")
                    yield f"event: error\ndata: {json.dumps({'error': 'Fatal connection error'})}\n\n"
            
            return EventSourceResponse(
                sse_generator(),
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "Content-Type": "text/event-stream",
                    "Access-Control-Allow-Origin": "*",
                    "Access-Control-Allow-Headers": "*",
                }
            )
        
        # Agregar endpoint OPTIONS para CORS preflight
        @app.options("/sse")
        async def sse_options():
            return Response(
                headers={
                    "Access-Control-Allow-Origin": "*",
                    "Access-Control-Allow-Methods": "POST, OPTIONS",
                    "Access-Control-Allow-Headers": "*",
                }
            )
        
        # Endpoint alternativo más simple para SSE
        @app.post("/sse-simple")
        async def handle_sse_simple(request: Request):
            """Implementación SSE más simple que podría funcionar mejor con Raycast - CON VALIDACIÓN"""
            
            if not self._initialized:
                self._initialize_server()
                
            logger.info("Conexión SSE simple iniciada")
            
            # VALIDAR JSON
            is_valid, json_data, error_response = await safe_read_and_validate_json(request)
            
            if not is_valid:
                logger.error(f"JSON inválido en /sse-simple: {error_response}")
                async def error_stream():
                    yield f"event: error\ndata: {json.dumps(create_json_error_response(error_response))}\n\n"
                
                return EventSourceResponse(
                    error_stream(),
                    headers={
                        "Cache-Control": "no-cache",
                        "Connection": "keep-alive", 
                        "Content-Type": "text/event-stream",
                        "Access-Control-Allow-Origin": "*",
                    }
                )
            
            async def simple_sse():
                try:
                    logger.info(f"JSON válido en /sse-simple: {json_data}")
                    
                    # Enviar mensaje de bienvenida
                    yield f"event: welcome\ndata: {json.dumps({'message': 'MCP SSE Server connected', 'received_data': json_data})}\n\n"
                    
                    # Listar herramientas disponibles
                    tools = await self.extract_tools_from_fastmcp()
                    tools_data = [{'name': t.name, 'description': t.description} for t in tools]
                    yield f"event: tools\ndata: {json.dumps({'tools': tools_data})}\n\n"
                    
                    # Mantener la conexión viva
                    counter = 0
                    while True:
                        counter += 1
                        yield f"event: ping\ndata: {json.dumps({'ping': counter, 'timestamp': str(asyncio.get_event_loop().time())})}\n\n"
                        await asyncio.sleep(30)  # Ping cada 30 segundos
                        
                except asyncio.CancelledError:
                    logger.info("Conexión SSE simple cancelada")
                    return
                except Exception as e:
                    logger.error(f"Error en SSE simple: {e}")
                    yield f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"
            
            return EventSourceResponse(
                simple_sse(),
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive", 
                    "Content-Type": "text/event-stream",
                    "Access-Control-Allow-Origin": "*",
                }
            )

        # Endpoint alternativo más simple para SSE
        @app.get("/sse-simple")
        async def handle_sse_simple_get(request: Request):
            """Implementación SSE más simple que podría funcionar mejor con Raycast - CON VALIDACIÓN"""
            
            if not self._initialized:
                self._initialize_server()
                
            logger.info(f"Conexión SSE simple iniciada: {dict(request.query_params)}")
            
            # VALIDAR JSON
            is_valid, json_data, error_response = await safe_read_and_validate_json(request)
            
            if not is_valid:
                logger.error(f"JSON inválido en /sse-simple: {error_response}")
                async def error_stream():
                    yield f"event: error\ndata: {json.dumps(create_json_error_response(error_response))}\n\n"
                
                return EventSourceResponse(
                    error_stream(),
                    headers={
                        "Cache-Control": "no-cache",
                        "Connection": "keep-alive", 
                        "Content-Type": "text/event-stream",
                        "Access-Control-Allow-Origin": "*",
                    }
                )
            
            async def simple_sse_get():
                try:
                    logger.info(f"JSON válido en /sse-simple: {json_data}")
                    
                    # Enviar mensaje de bienvenida
                    yield f"event: welcome\ndata: {json.dumps({'message': 'MCP SSE Server connected', 'received_data': json_data})}\n\n"
                    
                    # Listar herramientas disponibles
                    tools = await self.extract_tools_from_fastmcp()
                    tools_data = [{'name': t.name, 'description': t.description} for t in tools]
                    yield f"event: tools\ndata: {json.dumps({'tools': tools_data})}\n\n"
                    
                    # Mantener la conexión viva
                    counter = 0
                    while True:
                        counter += 1
                        yield f"event: ping\ndata: {json.dumps({'ping': counter, 'timestamp': str(asyncio.get_event_loop().time())})}\n\n"
                        await asyncio.sleep(30)  # Ping cada 30 segundos
                        
                except asyncio.CancelledError:
                    logger.info("Conexión SSE simple cancelada")
                    return
                except Exception as e:
                    logger.error(f"Error en SSE simple: {e}")
                    yield f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"


            #if "text/event-stream" in request.headers.get("accept", ""):
            if not self.first_get:
                logger.info(f"Petición GET con 'Accept: text/event-stream' recibida. Iniciando conexión SSE para cliente.")
                return EventSourceResponse(
                    simple_sse_get(),
                    headers={
                        "Cache-Control": "no-cache",
                        "Connection": "keep-alive", 
                        "Content-Type": "text/event-stream",
                        "Access-Control-Allow-Origin": "*",
                    }
                )
            else:
                # Esto podría ser un GET de "sondeo" (health check) u otro tipo de GET.
                # Simplemente responde con un 200 OK o un mensaje simple.
                logger.info(f"Petición GET sin 'Accept: text/event-stream' recibida. Respondiendo con 200 OK.")
                self.first_get = False
                #return Response(status_code=200, content="OK")
                return health_check()
            

        # Endpoint alternativo para testing básico de SSE
        @app.get("/sse-test")
        async def sse_test():
            """Endpoint de prueba para SSE básico"""
            async def test_stream():
                try:
                    for i in range(10):
                        yield f"data: {json.dumps({'message': f'Test message {i}', 'timestamp': str(asyncio.get_event_loop().time())})}\n\n"
                        await asyncio.sleep(1)
                except asyncio.CancelledError:
                    logger.info("Test stream cancelled")
                    return
                except Exception as e:
                    logger.error(f"Error in test stream: {e}")
                    yield f"data: {json.dumps({'error': str(e)})}\n\n"
            
            return EventSourceResponse(
                test_stream(),
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "Content-Type": "text/event-stream",
                }
            )
        
        return app

    async def run(self):
        """Ejecuta el servidor"""
        # Inicializar el servidor primero
        self._initialize_server()
        
        app = self.create_app()
        config = uvicorn.Config(
            app=app,
            host=self.host,
            port=self.port,
            log_level="info",
            access_log=True
        )
        server = uvicorn.Server(config)
        
        logger.info(f"Starting MCP SSE Server on {self.host}:{self.port}")
        logger.info(f"RAYCAST ENDPOINT: http://{self.host}:{self.port}/events")
        logger.info(f"SSE endpoint: http://{self.host}:{self.port}/sse")
        logger.info(f"SSE simple: http://{self.host}:{self.port}/sse-simple")
        logger.info(f"SSE test: http://{self.host}:{self.port}/sse-test")
        logger.info(f"Health check: http://{self.host}:{self.port}/health")
        logger.info(f"Tools debug: http://{self.host}:{self.port}/tools")
        
        # Mostrar herramientas disponibles al inicio
        try:
            tools = await self.extract_tools_from_fastmcp()
            logger.info(f"Loaded {len(tools)} tools:")
            for tool in tools:
                logger.info(f"  - {tool.name}: {tool.description}")
        except Exception as e:
            logger.error(f"Error loading tools: {e}")
        
        await server.serve()

def main():
    """Función principal"""
    parser = argparse.ArgumentParser(description="MCP SSE Server para Metricool con FastMCP")
    parser.add_argument("--host", default="0.0.0.0", help="Host del servidor")
    parser.add_argument("--port", type=int, default=8080, help="Puerto del servidor")
    parser.add_argument("--debug", action="store_true", help="Activar modo debug")
    
    args = parser.parse_args()
    
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
    
    # Crear y ejecutar el servidor
    server = McpSseServer(host=args.host, port=args.port)
    
    try:
        asyncio.run(server.run())
    except KeyboardInterrupt:
        logger.info("Servidor detenido por el usuario")
    except Exception as e:
        logger.error(f"Error ejecutando el servidor: {e}")

if __name__ == "__main__":
    main()