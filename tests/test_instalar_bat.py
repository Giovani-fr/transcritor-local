"""instalar.bat: mensagens e caminhos de falha, sem instalar nada de verdade.

O PC de teste nunca é tocado: "py" e "winget" são falsos (.bat numa pasta que entra primeiro no PATH),
LOCALAPPDATA/ProgramFiles/USERPROFILE apontam para pastas vazias e o PATH só tem a pasta dos falsos e uma pasta
"sistema" com cópias de where.exe e choice.exe (as únicas ferramentas do Windows que o .bat acha pelo PATH).
Assim o resultado não depende do que estiver instalado na máquina: no runner do GitHub, por exemplo, o py.exe fica na pasta do Windows e acha os Pythons de hostedtoolcache, o que não pode entrar no teste.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="instalar.bat só existe no Windows")


def rodar(tmp_path, *, py=None, winget=None, resposta="N"):
    """py/winget: texto de um .bat falso (None = não existe). Devolve (código, saída, pasta, chamadas_winget)."""
    projeto = tmp_path / "projeto"
    projeto.mkdir()
    for nome in ("instalar.bat", "requirements.txt", "requirements-gpu.txt"):
        (projeto / nome).write_bytes((RAIZ / nome).read_bytes())
    falsos = tmp_path / "falsos"
    falsos.mkdir()
    vazio = tmp_path / "vazio"
    vazio.mkdir()
    chamadas = tmp_path / "chamadas_winget.txt"
    if py is not None:
        (falsos / "py.bat").write_text(py, encoding="ascii")
    if winget is not None:
        (falsos / "winget.bat").write_text(winget.replace("@CHAMADAS@", str(chamadas)), encoding="ascii")
    windows = os.environ["SystemRoot"]
    sistema = tmp_path / "sistema"
    sistema.mkdir()
    for exe in ("where.exe", "choice.exe"):
        shutil.copy(Path(windows) / "System32" / exe, sistema / exe)
    env = {"SystemRoot": windows, "ComSpec": f"{windows}\\System32\\cmd.exe",
           "PATH": f"{falsos};{sistema}",
           "USERPROFILE": str(vazio), "LOCALAPPDATA": str(vazio), "ProgramFiles": str(vazio),
           "ProgramW6432": str(vazio), "TEMP": str(vazio), "INSTALAR_SEM_NAVEGADOR": "1",
           "INSTALAR_ATALHO_PASTA": str(vazio)}
    r = subprocess.run(["cmd", "/c", str(projeto / "instalar.bat")], env=env, input=resposta + "\r\n",
                       capture_output=True, text=True, timeout=120, cwd=tmp_path)
    return r.returncode, r.stdout, projeto, (chamadas.read_text() if chamadas.exists() else "")


PY_SO_315 = '@echo off\nif "%1"=="-0" (\n  echo  -V:3.15 *        C:\\fake\\python.exe\n  exit /b 0\n)\nexit /b 1\n'
PY_VARIOS = ('@echo off\nif "%1"=="-0" (\n  echo  -V:3.14 *        C:\\fake\\a.exe\n  echo  -3.13-64         C:\\fake\\b.exe\n'
             '  echo  -V:3.14-32       C:\\fake\\c.exe\n  exit /b 0\n)\nexit /b 1\n')
WINGET_FALSO = '@echo off\necho %* >> "@CHAMADAS@"\nexit /b 0\n'


def test_o_bat_usa_so_ascii():
    assert all(b < 128 for b in (RAIZ / "instalar.bat").read_bytes())


def test_o_bat_nao_tem_caracteres_de_controle():
    """Uma barra mais f digitada errada vira quebra de página e o comando fica "System32<FF>indstr.exe"."""
    dados = (RAIZ / "instalar.bat").read_bytes()
    assert not [b for b in dados if b < 32 and b not in (9, 10, 13)]


def test_o_resultado_nao_depende_do_py_instalado_na_maquina(tmp_path, monkeypatch):
    """Um py.exe real no PATH de quem roda o teste (como no runner do GitHub) não pode ser visto pelo .bat."""
    plantado = tmp_path / "plantado"
    plantado.mkdir()
    (plantado / "py.bat").write_text(f'@echo off\n"{sys.executable}" %2 %3 %4\n', encoding="ascii")
    monkeypatch.setenv("PATH", f"{plantado};{os.environ['PATH']}")
    sub = tmp_path / "t"
    sub.mkdir()
    codigo, saida, _, _ = rodar(sub)
    assert codigo == 1 and "Nao encontrei o Python neste computador" in saida


def test_sem_python_nenhum_e_sem_winget_manda_baixar_o_3_12(tmp_path):
    codigo, saida, projeto, _ = rodar(tmp_path)
    assert codigo == 1
    assert "Nao encontrei o Python neste computador" in saida
    assert "NAO a versao mais recente" in saida and "python.org" in saida
    assert "Traceback" not in saida
    assert "Instalacao interrompida" in (projeto / "instalar.log").read_text()


def test_so_o_python_3_15_diz_que_precisa_do_3_12(tmp_path):
    _, saida, _, _ = rodar(tmp_path, py=PY_SO_315)
    assert "Encontrei o Python 3.15, mas este programa precisa do 3.12" in saida


def test_lista_varias_versoes_sem_repetir_e_ignora_a_3_12(tmp_path):
    _, saida, _, _ = rodar(tmp_path, py=PY_VARIOS)
    assert "Encontrei o Python 3.14, 3.13, mas este programa precisa do 3.12" in saida


def test_com_winget_pergunta_e_nao_instala_se_o_usuario_diz_nao(tmp_path):
    codigo, saida, _, chamadas = rodar(tmp_path, py=PY_SO_315, winget=WINGET_FALSO, resposta="N")
    assert codigo == 1 and "nao instalei nada" in saida
    assert chamadas == ""


def test_com_winget_e_sim_chama_o_winget_certo_e_avisa_se_nao_terminou(tmp_path):
    codigo, saida, _, chamadas = rodar(tmp_path, py=PY_SO_315, winget=WINGET_FALSO, resposta="S")
    assert "install -e --id Python.Python.3.12" in chamadas
    assert codigo == 1 and "nao terminou" in saida and "Traceback" not in saida


def test_sem_requirements_orienta_a_extrair_o_zip(tmp_path):
    projeto = tmp_path / "p"
    projeto.mkdir()
    (projeto / "instalar.bat").write_bytes((RAIZ / "instalar.bat").read_bytes())
    r = subprocess.run(["cmd", "/c", str(projeto / "instalar.bat")], input="\r\n", capture_output=True, text=True,
                       timeout=60, env={**os.environ, "INSTALAR_SEM_NAVEGADOR": "1"})
    assert r.returncode == 1 and "extraia o ZIP inteiro" in r.stdout
