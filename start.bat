@echo off
echo ===================================================
echo Iniciando Ambiente Completo (Redis + API + Ngrok)...
echo ===================================================

echo [1] Subindo Banco de Dados Redis...
:: Inicia o Redis em uma nova janela
start "Redis Server" cmd /k ".\redis\redis-server.exe"

:: Pausa de 2 segundos para o Redis ligar
timeout /t 2 /nobreak >nul

echo [2] Subindo Servidor FastAPI...
:: Inicia a API
start "FastAPI Server" cmd /k ".\olx_scraper\venv\Scripts\python.exe -m uvicorn app:app --port 8000 --reload"

:: Pausa de 3 segundos para o servidor subir
timeout /t 3 /nobreak >nul

echo [3] Subindo Ngrok Tunnel...
:: Inicia o túnel de internet
start "Ngrok Tunnel" cmd /k "ngrok http 8000 --domain=radiated-choking-unguided.ngrok-free.dev"

echo.
echo ===================================================
echo TUDO PRONTO! 
echo O Redis, a API e o Ngrok estao rodando em segundo plano.
echo Pode fechar ESTA janela preta principal.
echo ===================================================
