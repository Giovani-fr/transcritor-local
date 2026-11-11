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
if not defined PORTA set "PORTA=8765"
set "SAIDAS=%TRANSCRITOR_SAIDAS%"
if not defined SAIDAS set "SAIDAS=%~dp0saidas"
set "CRASH=%SAIDAS%\crash.log"
set "REINICIOS=0"
rem abre o navegador depois de 2 s, quando o servidor ja estiver no ar
start "" cmd /c "timeout /t 2 /nobreak >nul & start http://127.0.0.1:%PORTA%"

:rodar
call :contar_quedas ANTES
"%VENV%\Scripts\python.exe" -X utf8 -m uvicorn transcritor.web.app:app --host 127.0.0.1 --port %PORTA% --timeout-graceful-shutdown 3
call :contar_quedas DEPOIS
rem So reinicia com EVIDENCIA de queda: o programa escreveu uma falha fatal no crash.log durante esta execucao.
rem Ctrl+C (uma ou duas vezes), fechar a janela ou qualquer saida sem essa evidencia NUNCA reiniciam.
if %DEPOIS% LEQ %ANTES% goto fim
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
exit /b 0

:contar_quedas
set "%~1=0"
if exist "%CRASH%" for /f "delims=" %%L in ('%SystemRoot%\System32\findstr.exe /c:"fatal exception" "%CRASH%"') do set /a %~1+=1
exit /b 0
