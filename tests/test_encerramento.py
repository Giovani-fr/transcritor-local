"""Encerrar o programa (Ctrl+C / Ctrl+Break) com a página aberta não pode travar em "Waiting for connections to close".

Sobe o uvicorn de verdade como subprocesso, abre uma conexão SSE (/api/eventos, igual à página) e envia
CTRL_BREAK_EVENT, que o uvicorn trata como o Ctrl+C no Windows. Nenhum microfone é aberto (captura simulada)
e o modelo de voz nem é carregado (pasta de modelos vazia).
"""
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Ctrl+Break do console só existe no Windows")
LIMITE_S = 8          # encerrar com a página aberta deve levar bem menos que isso


def _porta_livre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Servidor:
    def __init__(self, tmp_path, extra=()):
        self.porta = _porta_livre()
        self.saidas = tmp_path / "saidas"
        self.log_arq = tmp_path / "servidor.log"
        env = {**os.environ, "PYTHONPATH": str(RAIZ / "src"), "TRANSCRITOR_SAIDAS": str(self.saidas),
               "TRANSCRITOR_FORCAR_CPU": "1", "TRANSCRITOR_CAPTURA_SIMULADA": "1",
               "TRANSCRITOR_CACHE_MODELOS": str(tmp_path / "modelos-vazio")}
        self.proc = subprocess.Popen(
            [sys.executable, "-X", "utf8", "-m", "uvicorn", "transcritor.web.app:app", "--host", "127.0.0.1",
             "--port", str(self.porta), *extra],
            env=env, cwd=str(RAIZ), creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
            stdout=open(self.log_arq, "w", encoding="utf-8"), stderr=subprocess.STDOUT)
        fim = time.time() + 30
        while time.time() < fim:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{self.porta}/api/estado", timeout=1).read()
                return
            except OSError:
                time.sleep(0.2)
        self.matar()
        raise AssertionError("o servidor não subiu:\n" + self.log())

    def abrir_sse(self) -> socket.socket:
        """Conexão SSE aberta e mantida, como a da página no navegador."""
        s = socket.create_connection(("127.0.0.1", self.porta), timeout=5)
        s.sendall(b"GET /api/eventos HTTP/1.1\r\nHost: x\r\nAccept: text/event-stream\r\n\r\n")
        assert b"200" in s.recv(4096)
        return s

    def ctrl_break(self) -> None:
        self.proc.send_signal(signal.CTRL_BREAK_EVENT)

    def esperar_fim(self, limite_s: float) -> float | None:
        """Segundos até o processo sair, ou None se passou do limite."""
        t0 = time.time()
        try:
            self.proc.wait(limite_s)
        except subprocess.TimeoutExpired:
            return None
        return time.time() - t0

    def log(self) -> str:
        return self.log_arq.read_text(encoding="utf-8", errors="replace")

    def matar(self) -> None:
        if self.proc.poll() is None:
            self.proc.kill()
        self.proc.wait(5)


@pytest.fixture
def servidor(tmp_path):
    servidores = []

    def criar(extra=()):
        s = Servidor(tmp_path, extra)
        servidores.append(s)
        return s
    yield criar
    for s in servidores:
        s.matar()


def test_encerra_com_a_pagina_aberta(servidor):
    s = servidor()
    sse = s.abrir_sse()
    try:
        s.ctrl_break()
        tempo = s.esperar_fim(LIMITE_S)
        assert tempo is not None, "travou em 'Waiting for connections to close':\n" + s.log()
        assert "Traceback" not in s.log()
    finally:
        sse.close()


def test_varias_paginas_abertas_tambem_encerram(servidor):
    s = servidor()
    abertas = [s.abrir_sse() for _ in range(3)]
    try:
        s.ctrl_break()
        assert s.esperar_fim(LIMITE_S) is not None, s.log()
    finally:
        for a in abertas:
            a.close()


def test_ctrl_c_duplo_forca_a_saida_sem_travar(servidor):
    s = servidor()
    sse = s.abrir_sse()
    try:
        s.ctrl_break()
        time.sleep(0.3)
        s.ctrl_break()                                   # "forçar": segundo Ctrl+C seguido
        assert s.esperar_fim(LIMITE_S) is not None, s.log()
    finally:
        sse.close()
