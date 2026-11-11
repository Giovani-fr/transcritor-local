"""iniciar.bat: reinicia sozinho só em falha; nunca quando o programa é encerrado de propósito (Ctrl+C)."""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from transcritor.web.app import ARQ_ENCERRAMENTO, criar_app

from fastapi.testclient import TestClient
from test_servico import HW, ModeloFalso, Servico

RAIZ = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="iniciar.bat só existe no Windows")

# "uvicorn" falso: cenário vem de ROTEIRO (um item por execução): "cai" = erro sem marca; "ctrlc" = marca + erro; "ok" = sai bem
UVICORN_FALSO = '''
import os, pathlib, sys
base = pathlib.Path(os.environ["BASE_TESTE"])
n = len(list(base.glob("exec-*")))
(base / f"exec-{n}").write_text("")
passo = os.environ["ROTEIRO"].split(",")[min(n, len(os.environ["ROTEIRO"].split(",")) - 1)]
if passo == "ctrlc":
    (base / "saidas").mkdir(exist_ok=True)
    (base / "saidas" / ".encerrado-normalmente").write_text("x")
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
           "PYTHONHOME": sys.base_prefix}
    r = subprocess.run(["cmd", "/c", str(projeto / "iniciar.bat")], env=env, stdin=subprocess.DEVNULL,
                       capture_output=True, text=True, timeout=90)
    return len(list(projeto.glob("exec-*"))), r.stdout


def test_ctrl_c_encerra_de_proposito_e_nao_reinicia(tmp_path):
    execucoes, _ = rodar_bat(tmp_path, "ctrlc")           # sai com erro, mas deixou a marca de encerramento normal
    assert execucoes == 1


def test_saida_sem_erro_nao_reinicia(tmp_path):
    assert rodar_bat(tmp_path, "ok")[0] == 1


def test_falha_reinicia_e_para_quando_volta_ao_normal(tmp_path):
    execucoes, saida = rodar_bat(tmp_path, "cai,cai,ok")
    assert execucoes == 3 and "Reiniciando sozinho" in saida


def test_falha_sem_parar_desiste_depois_de_5_tentativas(tmp_path):
    execucoes, saida = rodar_bat(tmp_path, "cai")
    assert execucoes == 6 and "nao vou insistir" in saida


def test_encerramento_normal_do_app_deixa_a_marca(tmp_path):
    s = Servico(tmp_path / "saidas", HW, instalado=lambda n, c=None: True,
                carregar=lambda hw, c=None: (ModeloFalso(), hw))
    with TestClient(criar_app(s, iniciar=True)) as c:
        assert c.get("/api/estado").status_code == 200
        assert not (tmp_path / "saidas" / ARQ_ENCERRAMENTO).exists()      # rodando: sem marca
    assert (tmp_path / "saidas" / ARQ_ENCERRAMENTO).exists()              # fechou de propósito: com marca
