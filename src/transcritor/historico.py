"""Histórico: cada transcrição fica em uma pasta própria dentro de `saidas/`."""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from .texto import versao_do_txt

ARQ_TEXTO = "transcricao.txt"
ARQ_META = "meta.json"
_ID_VALIDO = re.compile(r"^[\w.\-]+$")


def _slug(nome: str, limite: int = 40) -> str:
    base = Path(nome).stem
    limpo = re.sub(r"[^\w\-]+", "-", base, flags=re.UNICODE).strip("-_")
    return (limpo or "sem-nome")[:limite]


class Historico:
    def __init__(self, pasta: Path):
        self.pasta = Path(pasta)

    def nova(self, nome_original: str) -> tuple[str, Path]:
        self.pasta.mkdir(parents=True, exist_ok=True)
        base = f"{datetime.now():%Y-%m-%d_%H-%M-%S}-{_slug(nome_original)}"
        id_, n = base, 1
        while (self.pasta / id_).exists():
            n += 1
            id_ = f"{base}-{n}"
        destino = self.pasta / id_
        destino.mkdir()
        return id_, destino

    @staticmethod
    def gravar_meta(pasta: Path, meta: dict) -> None:
        (pasta / ARQ_META).write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    def caminho(self, id_: str) -> Path | None:
        """Pasta de uma transcrição, ou None se o id for inválido/inexistente (evita '..')."""
        if not _ID_VALIDO.match(id_) or id_ in (".", ".."):
            return None
        p = self.pasta / id_
        return p if p.is_dir() else None

    def listar(self) -> list[dict]:
        itens = []
        if not self.pasta.is_dir():
            return itens
        for p in sorted((d for d in self.pasta.iterdir() if d.is_dir()), reverse=True):
            try:
                meta = json.loads((p / ARQ_META).read_text(encoding="utf-8"))
            except Exception:
                continue
            if meta.get("status") == "em_andamento" and not (p / ARQ_TEXTO).exists():
                continue
            if meta.get("status") == "em_andamento":
                meta["status"] = "interrompido"     # o programa foi fechado no meio
            itens.append({**meta, "id": p.name})
        return itens

    def texto(self, id_: str, horarios: bool = True) -> str | None:
        p = self.caminho(id_)
        if p is None or not (p / ARQ_TEXTO).exists():
            return None
        return versao_do_txt((p / ARQ_TEXTO).read_text(encoding="utf-8"), horarios)

    def nome_amigavel(self, id_: str) -> str:
        for item in self.listar():
            if item["id"] == id_:
                return Path(item.get("nome", id_)).stem or id_
        return id_
