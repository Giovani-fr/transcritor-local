"""iniciar.bat: reinicia sozinho só em falha; nunca quando o programa é encerrado de propósito (Ctrl+C)."""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="iniciar.bat só existe no Windows")

# "uvicorn" falso: o cenário de cada execução vem de ROTEIRO (separado por vírgulas):
#   cai     = falha fatal registrada no crash.log (evidência de queda) e saída com erro
#   forcado = Ctrl+C duplo / janela fechada à força: sai com erro e SEM nada no crash.log
#   ok      = sai sem erro
UVICORN_FALSO = '''
import os, pathlib, sys
base = pathlib.Path(os.environ["BASE_TESTE"])
n = len(list(base.glob("exec-*")))
(base / f"exec-{n}").write_text("")
passos = os.environ["ROTEIRO"].split(",")
passo = passos[min(n, len(passos) - 1)]
saidas = pathlib.Path(os.environ["TRANSCRITOR_SAIDAS"])
saidas.mkdir(exist_ok=True)
with open(saidas / "crash.log", "a") as f:
    f.write("--- processo iniciado ---\\n")                       # o cabeçalho de toda execução
    if passo == "cai":
        f.write("Windows fatal exception: access violation\\n")
sys.exit(0 if passo == "ok" else 1)
'''


def rodar_bat(tmp_path, roteiro):
    casa = tmp_path / "casa"
    py = casa / "venvs" / "transcritor-local" / "Scripts"
    py.mkdir(parents=True)
    shutil.copy(Path(sys.base_prefix) / "python.exe", py / "python.exe")
    for dll in Path(sys.base_prefix).glob("python3*.dll"):
        shutil.copy(dll, py / dll.name)
    projeto = tmp_path / "base"
    (projeto / "src" / "uvicorn").mkdir(parents=True)
    (projeto / "src" / "uvicorn" / "__init__.py").write_text("")
    (projeto / "src" / "uvicorn" / "__main__.py").write_text(UVICORN_FALSO)
    texto = (RAIZ / "iniciar.bat").read_text(encoding="ascii")
    texto = "\n".join(l for l in texto.splitlines() if not l.startswith('start ""'))      # sem abrir o navegador
    (projeto / "iniciar.bat").write_text(texto + "\n", encoding="ascii")
    env = {**__import__("os").environ, "USERPROFILE": str(casa), "ROTEIRO": roteiro, "BASE_TESTE": str(projeto),
           "PYTHONHOME": sys.base_prefix,
           "TRANSCRITOR_SAIDAS": str(projeto / "saidas")}
    r = subprocess.run(["cmd", "/c", str(projeto / "iniciar.bat")], env=env, stdin=subprocess.DEVNULL,
                       capture_output=True, text=True, timeout=90)
    return len(list(projeto.glob("exec-*"))), r.stdout


def test_ctrl_c_ou_ctrl_c_duplo_nunca_reinicia(tmp_path):
    """Saída com erro mas sem evidência de queda (Ctrl+C, Ctrl+C duplo para forçar, janela fechada)."""
    execucoes, saida = rodar_bat(tmp_path, "forcado")
    assert execucoes == 1 and "Reiniciando" not in saida


def test_saida_sem_erro_nao_reinicia(tmp_path):
    assert rodar_bat(tmp_path, "ok")[0] == 1


def test_queda_com_evidencia_reinicia_e_para_quando_volta_ao_normal(tmp_path):
    execucoes, saida = rodar_bat(tmp_path, "cai,cai,ok")
    assert execucoes == 3 and "Reiniciando sozinho" in saida


def test_queda_depois_ctrl_c_duplo_nao_reinicia_de_novo(tmp_path):
    """Caiu uma vez (reinicia) e na volta o usuário forçou a saída: a queda antiga não conta."""
    execucoes, _ = rodar_bat(tmp_path, "cai,forcado")
    assert execucoes == 2


def test_queda_sem_parar_desiste_depois_de_5_tentativas(tmp_path):
    execucoes, saida = rodar_bat(tmp_path, "cai")
    assert execucoes == 6 and "nao vou insistir" in saida


def test_o_bat_usa_so_ascii_e_o_limite_de_encerramento():
    texto = (RAIZ / "iniciar.bat").read_bytes()
    assert all(b < 128 for b in texto) and b"--timeout-graceful-shutdown 3" in texto


def test_sem_ambiente_manda_rodar_o_instalar_bat_sem_mostrar_comandos(tmp_path):
    projeto = tmp_path / "projeto"
    projeto.mkdir()
    (projeto / "iniciar.bat").write_bytes((RAIZ / "iniciar.bat").read_bytes())
    casa = tmp_path / "casa"
    casa.mkdir()
    r = subprocess.run(["cmd", "/c", str(projeto / "iniciar.bat")], env={**__import__("os").environ, "USERPROFILE": str(casa)},
                       input="\r\n", capture_output=True, text=True, timeout=60)
    assert r.returncode == 1 and "Rode primeiro o instalar.bat" in r.stdout
    assert "py -3.12" not in r.stdout and "pip" not in r.stdout
