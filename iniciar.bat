@echo off
rem Inicia o transcritor-local e abre o navegador. (Arquivo so com ASCII de proposito.)
rem O venv fica fora do OneDrive para nao sincronizar varios GB.
set "VENV=%USERPROFILE%\venvs\transcritor-local"
if not exist "%VENV%\Scripts\python.exe" (
    echo Ambiente nao encontrado em %VENV%
    echo Rode antes:  py -3.12 -m venv "%VENV%"  e  "%VENV%\Scripts\pip" install -r requirements.txt
    pause
    exit /b 1
)
cd /d "%~dp0"
set "PYTHONPATH=%~dp0src"
set "REINICIOS=0"
rem abre o navegador depois de 2 s, quando o servidor ja estiver no ar
start "" cmd /c "timeout /t 2 /nobreak >nul & start http://127.0.0.1:8765"

:rodar
"%VENV%\Scripts\python.exe" -X utf8 -m uvicorn transcritor.web.app:app --host 127.0.0.1 --port 8765
if not errorlevel 1 goto fim
set /a REINICIOS+=1
if %REINICIOS% GTR 5 goto desistiu
echo.
echo ================================================================
echo  O programa fechou por causa de um erro. Reiniciando sozinho...
echo  (tentativa %REINICIOS% de 5) Seus audios e textos anteriores estao salvos.
echo  A pagina no navegador volta sozinha em alguns segundos.
echo ================================================================
timeout /t 3 /nobreak >nul
goto rodar

:desistiu
echo.
echo O programa fechou varias vezes seguidas e nao vou insistir.
echo Feche esta janela e abra o iniciar.bat de novo.
echo O detalhe tecnico esta na pasta saidas (registro.log e crash.log).

:fim
pause
