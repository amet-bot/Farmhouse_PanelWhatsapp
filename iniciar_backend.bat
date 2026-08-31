@echo off
title Farmhouse Backend Server (FastAPI + MySQL)
color 0A
echo ===================================================
echo     FARMHOUSE WHATSAPP CENTER - SERVIDOR BACKEND
echo ===================================================
echo.
cd /d "%~dp0backend"
echo Iniciando servidor FastAPI en http://localhost:8000 ...
echo Presiona CTRL+C para detener el servidor.
echo.
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
pause
