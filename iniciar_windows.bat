@echo off
echo ============================================
echo   InmoPosadas - Iniciando servidor
echo ============================================
echo.

REM Instalar dependencias si no existen
pip show fastapi >nul 2>&1
IF %ERRORLEVEL% NEQ 0 (
    echo Instalando dependencias...
    pip install fastapi uvicorn httpx aiofiles python-dotenv
)

echo Servidor disponible en: http://localhost:8000
echo.
echo El primer inicio ejecutara el scraping automaticamente.
echo Puede tardar 2-5 minutos en obtener todos los datos.
echo.
echo Presiona Ctrl+C para detener.
echo.

cd /d "%~dp0"
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
pause
