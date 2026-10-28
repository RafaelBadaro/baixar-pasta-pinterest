@echo off
cd /d "%~dp0"

rem Se o servidor ja estiver rodando, so abre o navegador.
curl -s -o nul http://127.0.0.1:8766
if %errorlevel%==0 (
  start http://127.0.0.1:8766
  exit /b
)

rem Instala o que faltar (so na primeira vez).
python --version >nul 2>&1
if errorlevel 1 (
  winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
  echo.
  echo Instalacao concluida! Feche esta janela e de dois cliques em "Abrir Baixador.bat" de novo.
  pause
  exit /b
)
python -m gallery_dl --version >nul 2>&1 || python -m pip install -U gallery-dl

python server.py
pause
