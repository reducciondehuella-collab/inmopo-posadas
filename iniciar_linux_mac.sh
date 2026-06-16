#!/bin/bash
echo "============================================"
echo "  InmoPosadas - Iniciando servidor"
echo "============================================"
echo ""

# Instalar dependencias si hace falta
pip show fastapi >/dev/null 2>&1 || pip install fastapi uvicorn httpx aiofiles python-dotenv

echo "Servidor disponible en: http://localhost:8000"
echo ""
echo "El primer inicio ejecutará el scraping automáticamente."
echo "Puede tardar 2-5 minutos en obtener todos los datos."
echo ""
echo "Presioná Ctrl+C para detener."
echo ""

cd "$(dirname "$0")"
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
