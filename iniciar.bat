@echo off
chcp 65001 >nul
rem Inicia o transcritor-local e abre o navegador.
rem O venv fica fora do OneDrive para não sincronizar vários GB.
set "VENV=%USERPROFILE%\venvs\transcritor-local"
if not exist "%VENV%\Scripts\python.exe" (
    echo Ambiente nao encontrado em %VENV%
    echo Rode antes:  py -3.12 -m venv "%VENV%"  e  "%VENV%\Scripts\pip" install -r requirements.txt
    pause
    exit /b 1
)
cd /d "%~dp0"
set "PYTHONPATH=%~dp0src"
rem abre o navegador depois de 2 s, quando o servidor ja estiver no ar
start "" cmd /c "timeout /t 2 /nobreak >nul & start http://127.0.0.1:8765"
"%VENV%\Scripts\python.exe" -m uvicorn transcritor.web.app:app --host 127.0.0.1 --port 8765
pause
