"""Configuração: pastas e ajustes opcionais por variável de ambiente."""
from __future__ import annotations

import os
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]


def pasta_saidas() -> Path:
    """Onde ficam as transcrições (histórico). Padrão: pasta `saidas/` do projeto."""
    return Path(os.environ.get("TRANSCRITOR_SAIDAS") or RAIZ / "saidas")


def pasta_modelos() -> str | None:
    """Onde ficam os modelos baixados. None = cache padrão do Hugging Face."""
    return os.environ.get("TRANSCRITOR_CACHE_MODELOS") or None


def forcar_cpu() -> bool:
    return os.environ.get("TRANSCRITOR_FORCAR_CPU", "") in ("1", "true", "sim")


def modelo_forcado() -> str | None:
    return os.environ.get("TRANSCRITOR_MODELO") or None


def cpu_threads() -> int:
    try:
        return int(os.environ.get("TRANSCRITOR_CPU_THREADS", "0"))
    except ValueError:
        return 0
