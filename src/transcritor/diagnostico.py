"""Diagnóstico de travamentos nativos: grava a pilha de todas as threads em ``saidas/<nome>`` se o Python cair."""
from __future__ import annotations

import faulthandler
import time
from pathlib import Path

_arquivo = None


def ativar(pasta: Path, nome: str = "crash.log"):
    """Liga o faulthandler gravando em ``pasta/nome`` (modo append). Seguro chamar mais de uma vez."""
    global _arquivo
    desativar()
    try:
        Path(pasta).mkdir(parents=True, exist_ok=True)
        _arquivo = open(Path(pasta) / nome, "a", encoding="utf-8", buffering=1)
        _arquivo.write(f"\n--- {time.strftime('%Y-%m-%d %H:%M:%S')}: processo iniciado ---\n")
        faulthandler.enable(_arquivo, all_threads=True)
    except OSError:
        _arquivo = None
    return _arquivo


def desativar() -> None:
    global _arquivo
    if _arquivo is None:
        return
    try:
        faulthandler.disable()
        _arquivo.close()
    except (OSError, ValueError):
        pass
    _arquivo = None
