#!/bin/bash
set -ex

# Script para iniciar el MCP Metricool SSE Server


export METRICOOL_USER_TOKEN="VDGVECPTRGOYJBGIRNCNTKCAWMYMBHHZIGZGGUNVWQAVANLUTFAXPUFIQTBICKXW"
export METRICOOL_USER_ID="1038075"

# Colores para output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${GREEN}🚀 Iniciando MCP Metricool SSE Server${NC}"

# Verificar que existe el API key
if [ -z "$METRICOOL_USER_TOKEN" ]; then
    echo -e "${RED}❌ Error: METRICOOL_API_KEY no está configurada${NC}"
    echo "Configura tu API key con:"
    echo "export METRICOOL_API_KEY='tu_api_key_aqui'"
    exit 1
fi

# Configuración del servidor
HOST=${HOST:-"0.0.0.0"}
PORT=${PORT:-"8000"}

echo -e "${YELLOW}📡 Configuración del servidor:${NC}"
echo "  Host: $HOST"
echo "  Port: $PORT"
echo "  API Key: ${METRICOOL_USER_TOKEN:0:10}..."

# Crear entorno virtual si no existe
if [ ! -d "venv" ]; then
    echo -e "${YELLOW}📦 Creando entorno virtual...${NC}"
    python3 -m venv venv
fi

# Activar entorno virtual
echo -e "${YELLOW}🔧 Activando entorno virtual...${NC}"
source venv/bin/activate

# Instalar dependencias
echo -e "${YELLOW}📚 Instalando dependencias...${NC}"
pip install -e .

# Ejecutar el servidor
echo -e "${GREEN}🌟 Iniciando servidor en http://$HOST:$PORT${NC}"
echo -e "${YELLOW}📋 Endpoints disponibles:${NC}"
echo "  • GET  / - Estado del servidor"
echo "  • POST /sse - Endpoint SSE para MCP"
echo ""
echo -e "${YELLOW}Para detener el servidor, presiona Ctrl+C${NC}"
echo ""

python3 src/mcp_metricool/mcp_sse_server.py