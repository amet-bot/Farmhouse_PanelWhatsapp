@echo off
title Auto Git Sync - Farmhouse WhatsApp Center
echo =================================================================
echo  Iniciando sincronizador automatico con GitHub...
echo  Cualquier cambio guardado en el codigo se subira a GitHub.
echo =================================================================
echo.

cd /d "%~dp0"
py -3.12 backend/scripts/auto_git_sync.py
pause
