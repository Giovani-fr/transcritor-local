"""Instalação do modelo Whisper: verificar se já está no PC e baixar com progresso."""
from __future__ import annotations

import fnmatch
import os
import threading
from typing import Callable

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

# Tamanhos aproximados (MB), usados antes de saber o tamanho exato do download.
TAMANHO_MB = {"tiny": 75, "base": 145, "small": 485, "medium": 1530,
              "large-v3": 3100, "large-v3-turbo": 1620}
PADROES = ["config.json", "preprocessor_config.json", "model.bin", "tokenizer.json", "vocabulary.*"]


_SILENCIO = open(os.devnull, "w")


def tamanho_aproximado_mb(nome: str) -> int:
    return TAMANHO_MB.get(nome, 500)


def repo_do_modelo(nome: str) -> str:
    try:
        from faster_whisper.utils import _MODELS
        return _MODELS.get(nome) or f"Systran/faster-whisper-{nome}"
    except Exception:
        return f"Systran/faster-whisper-{nome}"


def _casa(arquivo: str) -> bool:
    return any(fnmatch.fnmatch(arquivo, p) for p in PADROES)


def instalado(nome: str, cache_dir: str | None = None) -> bool:
    """True se o modelo já está completo no PC (não usa a internet)."""
    from faster_whisper.utils import download_model
    try:
        pasta = download_model(nome, local_files_only=True, cache_dir=cache_dir)
    except Exception:
        return False
    return os.path.exists(os.path.join(pasta, "model.bin"))


def baixar(nome: str, ao_progresso: Callable[[float, float, float], None],
           cache_dir: str | None = None) -> None:
    """Baixa o modelo. ``ao_progresso(fração 0-1, baixado_MB, total_MB)``. Levanta exceção se falhar."""
    from huggingface_hub import HfApi, snapshot_download
    from tqdm.auto import tqdm

    repo = repo_do_modelo(nome)
    try:
        info = HfApi().model_info(repo, files_metadata=True)
        total = sum((s.size or 0) for s in info.siblings if _casa(s.rfilename))
    except Exception:
        total = 0
    total = total or tamanho_aproximado_mb(nome) * 1024 ** 2

    lock = threading.Lock()
    barras: set = set()
    ultimo = [-1.0]

    def avisar():
        with lock:
            feito = min(sum(b.n for b in barras), total)
        frac = min(feito / total, 1.0)
        if frac - ultimo[0] >= 0.005:
            ultimo[0] = frac
            ao_progresso(frac, feito / 1024 ** 2, total / 1024 ** 2)

    class Barra(tqdm):
        """Barra do Hugging Face que só reporta bytes realmente baixados (sem imprimir no console)."""

        def __init__(self, *a, **k):
            k["disable"] = False
            k["file"] = _SILENCIO
            super().__init__(*a, **k)
            d = str(self.desc or "").lower()
            # o backend hf_xet cria barras extras ("Reconstructing", "Download complete") que repetem os bytes
            self._conta = self.unit == "B" and "reconstruct" not in d and "complete" not in d
            if self._conta:
                with lock:
                    barras.add(self)

        def update(self, n=1):
            r = super().update(n)
            if self._conta:
                avisar()
            return r

    ao_progresso(0.0, 0.0, total / 1024 ** 2)
    snapshot_download(repo, allow_patterns=PADROES, cache_dir=cache_dir, tqdm_class=Barra)
    ao_progresso(1.0, total / 1024 ** 2, total / 1024 ** 2)
