@echo off
cd /d "%~dp0"
set SERVICE_API_KEY=mi_clave_123
echo Iniciando el servidor de Python...
start cmd /k "C:\Users\Adcris\AppData\Local\Python\pythoncore-3.14-64\python.exe -m uvicorn main:app --reload --port 8000"
echo Iniciando Ngrok...
start cmd /k "ngrok http 8000"
pause