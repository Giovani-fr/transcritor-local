@echo off
setlocal EnableExtensions EnableDelayedExpansion
rem Instala o transcritor-local do zero. (Arquivo so com ASCII de proposito.)
rem Procura o Python 3.12, instala pelo winget se faltar (com a sua confirmacao), cria o ambiente,
rem instala as bibliotecas e cria um atalho na area de trabalho. Tudo fica gravado em instalar.log.
rem O ambiente fica fora do OneDrive para nao sincronizar varios GB.

set "PYVER=3.12"
set "PYNUM=312"
set "VENV=%USERPROFILE%\venvs\transcritor-local"
set "RAIZ=%~dp0"
set "LOG=%RAIZ%instalar.log"
set "PYEXE="

> "%LOG%" echo Instalacao iniciada em %DATE% %TIME%
>> "%LOG%" echo Pasta: %RAIZ%
cd /d "%RAIZ%"
if exist "%RAIZ%requirements.txt" goto tem_req
echo.
echo Nao achei o arquivo requirements.txt nesta pasta.
echo O que fazer: extraia o ZIP inteiro e rode o instalar.bat de dentro da pasta extraida.
goto fim_erro
:tem_req

echo.
echo ================================================================
echo  Instalador do Transcritor local
echo  Vai levar alguns minutos. Pode deixar esta janela aberta.
echo  Tudo o que acontece fica gravado no arquivo instalar.log.
echo ================================================================
echo.

rem ---------- 1. Procurar o Python 3.12 ----------
echo [1/5] Procurando o Python %PYVER% neste computador...
call :procurar_python
if defined PYEXE goto achou_python

call :listar_outros
echo.
if not defined OUTROS goto nenhum_python
echo Encontrei o Python !OUTROS!, mas este programa precisa do %PYVER%.
echo Ter outro Python, mesmo mais novo, nao basta: as bibliotecas de voz so funcionam no %PYVER%.
goto msg_python_ok
:nenhum_python
echo Nao encontrei o Python neste computador.
:msg_python_ok
>> "%LOG%" echo Python %PYVER% nao encontrado. Outros: !OUTROS!
echo O Python %PYVER% pode ficar junto com os outros sem atrapalhar nada.
echo.

where winget >nul 2>&1
if errorlevel 1 goto sem_winget

echo Posso instalar o Python %PYVER% agora, pelo instalador oficial do Windows (winget).
echo Ele baixa cerca de 25 MB e nao mexe nos outros Pythons.
choice /c SN /n /m "Instalar o Python %PYVER% agora? Digite S para sim ou N para nao: "
if errorlevel 2 goto recusou
echo.
echo Instalando o Python %PYVER%... aguarde, pode aparecer uma janela de permissao do Windows.
>> "%LOG%" echo Rodando: winget install -e --id Python.Python.%PYVER%
call winget install -e --id Python.Python.%PYVER% --accept-package-agreements --accept-source-agreements >> "%LOG%" 2>&1
>> "%LOG%" echo winget terminou com codigo %ERRORLEVEL%
rem Procura pelo caminho completo: nao precisa reiniciar o Windows nem atualizar o PATH.
call :procurar_python
if defined PYEXE goto achou_python
echo.
echo A instalacao do Python %PYVER% nao terminou.
echo O que fazer: aceite a janela de permissao do Windows se ela aparecer e rode o instalar.bat de novo.
echo Se continuar assim, instale o Python %PYVER% pelo site: https://www.python.org/downloads/windows/
echo Baixe a versao %PYVER%, nao a mais recente. Depois rode o instalar.bat de novo.
goto fim_erro

:sem_winget
echo Este Windows nao tem o winget, entao nao consigo instalar o Python sozinho.
echo Vou abrir a pagina de download. Siga estes passos:
echo   1. Baixe o Python %PYVER%.x, NAO a versao mais recente.
echo   2. Abra o arquivo baixado e clique em "Install Now".
echo   3. Quando terminar, rode o instalar.bat de novo.
echo Se a pagina nao abrir, o endereco e: https://www.python.org/downloads/windows/
>> "%LOG%" echo Sem winget: abrindo a pagina de download.
if not defined INSTALAR_SEM_NAVEGADOR start "" "https://www.python.org/downloads/windows/"
goto fim_erro

:recusou
echo.
echo Tudo bem, nao instalei nada. Sem o Python %PYVER% o programa nao funciona.
echo O que fazer: instale o Python %PYVER% pelo site https://www.python.org/downloads/windows/
echo (baixe a versao %PYVER%, nao a mais recente) e rode o instalar.bat de novo.
>> "%LOG%" echo Usuario recusou instalar o Python pelo winget.
goto fim_erro

:achou_python
echo       Python %PYVER% encontrado.
>> "%LOG%" echo Python: %PYEXE%

rem ---------- 2. Criar o ambiente ----------
echo.
echo [2/5] Criando o ambiente do programa...
set "VENVPY=%VENV%\Scripts\python.exe"
set "RECRIAR="
if not exist "%VENVPY%" goto criar_venv
call :confere_versao "%VENVPY%"
if errorlevel 1 goto venv_antigo
echo       O ambiente ja existe; vou reaproveitar.
goto venv_pronto

:venv_antigo
echo       O ambiente antigo e de outra versao do Python. Vou refazer.
set "RECRIAR=--clear"

:criar_venv
>> "%LOG%" echo Criando venv em %VENV%
"%PYEXE%" -m venv %RECRIAR% "%VENV%" >> "%LOG%" 2>&1
if errorlevel 1 goto falha_venv

:venv_pronto
if not exist "%VENVPY%" goto falha_venv

rem ---------- 3. Bibliotecas ----------
echo.
echo [3/5] Instalando as bibliotecas (cerca de 0,6 GB; pode levar de 3 a 10 minutos)...
>> "%LOG%" echo Atualizando o pip
"%VENVPY%" -m pip install --disable-pip-version-check --upgrade pip >> "%LOG%" 2>&1
>> "%LOG%" echo Instalando requirements.txt
"%VENVPY%" -m pip install --disable-pip-version-check -r "%RAIZ%requirements.txt" >> "%LOG%" 2>&1
if errorlevel 1 goto falha_pip
echo       Bibliotecas instaladas.

rem ---------- 4. Placa Nvidia ----------
echo.
echo [4/5] Procurando placa de video Nvidia...
set "TEMPLACA="
nvidia-smi -L >nul 2>&1
if not errorlevel 1 set "TEMPLACA=1"
if defined TEMPLACA goto com_placa
echo       Nao encontrei placa Nvidia. Sem problema: o programa usa o processador.
>> "%LOG%" echo Sem placa Nvidia.
goto atalho

:com_placa
echo       Encontrei uma placa Nvidia. Vou instalar tambem as bibliotecas para usa-la.
echo       Atencao: sao cerca de 2 GB e pode levar de 5 a 15 minutos, dependendo da internet.
>> "%LOG%" echo Placa Nvidia encontrada: instalando requirements-gpu.txt
"%VENVPY%" -m pip install --disable-pip-version-check -r "%RAIZ%requirements-gpu.txt" >> "%LOG%" 2>&1
if errorlevel 1 goto falha_gpu
echo       Bibliotecas da placa de video instaladas.
goto atalho

:falha_gpu
echo.
echo Nao consegui instalar as bibliotecas da placa de video.
echo O programa vai funcionar normalmente, so que mais devagar, usando o processador.
echo O que fazer: para tentar de novo mais tarde, rode o instalar.bat outra vez.
echo Mensagens de erro encontradas:
%SystemRoot%\System32indstr.exe /b /c:"ERROR" "%LOG%"

rem ---------- 5. Atalho ----------
:atalho
echo.
echo [5/5] Criando o atalho "Transcritor local" na area de trabalho...
set "INSTALAR_PROJ=%RAIZ%"
%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "try { $d = $env:INSTALAR_ATALHO_PASTA; if (-not $d) { $d = [Environment]::GetFolderPath('Desktop') }; $s = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $d 'Transcritor local.lnk')); $s.TargetPath = (Join-Path $env:INSTALAR_PROJ 'iniciar.bat'); $s.WorkingDirectory = $env:INSTALAR_PROJ; $s.Description = 'Transcritor local'; $s.Save(); exit 0 } catch { Write-Output $_.Exception.Message; exit 1 }" >> "%LOG%" 2>&1
if errorlevel 1 goto sem_atalho
echo       Atalho criado.
goto pronto

:sem_atalho
echo       Nao consegui criar o atalho. Sem problema: use o iniciar.bat desta pasta.

:pronto
echo.
echo ================================================================
echo  Pronto. Para abrir o programa, use o atalho "Transcritor local"
echo  na area de trabalho ou de dois cliques em iniciar.bat.
echo  Na primeira vez ele pede para baixar o modelo de voz.
echo ================================================================
>> "%LOG%" echo Instalacao concluida.
echo.
pause
exit /b 0

:falha_venv
echo.
echo Nao consegui criar o ambiente em %VENV%.
echo O que fazer: confira se ha espaco livre no disco C: e se o antivirus nao esta bloqueando. Rode o instalar.bat de novo.
goto fim_erro

:falha_pip
echo.
echo A instalacao das bibliotecas falhou.
echo O que fazer: confira se o computador esta com internet e rode o instalar.bat de novo.
echo Ele continua de onde parou. Se falhar outra vez, mande o arquivo instalar.log para quem te ajuda.
echo Mensagens de erro encontradas:
%SystemRoot%\System32indstr.exe /b /c:"ERROR" "%LOG%"
goto fim_erro

:fim_erro
>> "%LOG%" echo Instalacao interrompida.
echo.
echo A instalacao nao terminou. O registro esta em: %LOG%
echo.
pause
exit /b 1

rem ---------------- rotinas ----------------

:procurar_python
rem Define PYEXE com o caminho completo de um Python 3.12 de 64 bits que funcione.
set "PYEXE="
set "ACHADO="
where py >nul 2>&1
if errorlevel 1 goto sem_py_launcher
for /f "usebackq delims=" %%P in (`py -%PYVER% -c "import sys; print(sys.executable)" 2^>nul`) do set "ACHADO=%%P"
if defined ACHADO call :testa_exe "!ACHADO!"
:sem_py_launcher
if defined PYEXE exit /b 0
call :testa_exe "%LOCALAPPDATA%\Programs\Python\Python%PYNUM%\python.exe"
if defined PYEXE exit /b 0
call :testa_exe "%LOCALAPPDATA%\Python\pythoncore-%PYVER%-64\python.exe"
if defined PYEXE exit /b 0
call :testa_exe "%ProgramFiles%\Python%PYNUM%\python.exe"
if defined PYEXE exit /b 0
call :testa_exe "%ProgramW6432%\Python%PYNUM%\python.exe"
exit /b 0

:testa_exe
rem %~1 = caminho de um python.exe. Se for o 3.12 de 64 bits e funcionar, vira o PYEXE.
if not exist "%~1" exit /b 1
"%~1" -c "import sys, struct; sys.exit(0 if sys.version_info[:2] == (3, 12) and struct.calcsize('P') == 8 else 1)" >nul 2>&1
if errorlevel 1 exit /b 1
set "PYEXE=%~1"
exit /b 0

:confere_versao
rem %~1 = python.exe do ambiente. Erro (1) se nao for o 3.12.
"%~1" -c "import sys; sys.exit(0 if sys.version_info[:2] == (3, 12) else 1)" >nul 2>&1
exit /b %ERRORLEVEL%

:listar_outros
rem Define OUTROS com as versoes de Python que o py enxerga (ex.: 3.15 ou 3.14, 3.15).
set "OUTROS="
where py >nul 2>&1
if errorlevel 1 exit /b 0
for /f "tokens=1" %%A in ('py -0 2^>nul') do call :uma_versao "%%A"
exit /b 0

:uma_versao
set "V=%~1"
set "V=!V:-V:=!"
if "!V:~0,1!"=="-" set "V=!V:~1!"
for /f "delims=-" %%B in ("!V!") do set "V=%%B"
echo !V!| %SystemRoot%\System32\findstr.exe /r /c:"^[0-9][0-9]*\.[0-9][0-9]*$" >nul || exit /b 0
if "!V!"=="%PYVER%" exit /b 0
if not defined OUTROS (
    set "OUTROS=!V!"
    exit /b 0
)
if "!OUTROS:%V%=!"=="!OUTROS!" set "OUTROS=!OUTROS!, !V!"
exit /b 0
