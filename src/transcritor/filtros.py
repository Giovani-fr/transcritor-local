"""Filtros de texto: alucinações do Whisper e linhas só de pontuação.

O Whisper "inventa" frases como "Obrigado." quando ouve só silêncio ou ruído.
Estas funções descartam esses trechos e marcam falas baixas como ``(fundo)``.
"""
from __future__ import annotations

import re

import numpy as np

# Frases que o Whisper costuma inventar sobre ruído/silêncio (minúsculas, sem pontuação).
ALUCINACOES = {
    # português
    "obrigado", "obrigada", "obrigado a todos", "muito obrigado", "tchau",
    "tchau tchau", "até a próxima", "até o próximo vídeo", "inscreva-se",
    "legendas pela comunidade amara.org", "legenda adriana zanotto",
    # inglês
    "thank you", "thank you very much", "thanks for watching", "thank you for watching",
    "bye", "bye bye", "see you next time", "subscribe",
    # espanhol
    "gracias", "muchas gracias", "hasta la próxima",
}


# Créditos de legenda inventados ("Legendas por Fulano", "Subtitles by...", "Tradução e legendas...").
# Só em linhas curtas, para não descartar fala real que comece com "legendas ..." / "subtitles ...".
_CREDITO = re.compile(
    r"^((tradu[cç][aã]o e )?legendas? (por|pela|pelo)|subtitles? by|captions? by|subt[ií]tulos? por)\b|amara\.org",
    re.IGNORECASE,
)
_CREDITO_MAX_CHARS = 60


def _normalizar(texto: str) -> str:
    return "".join(ch for ch in texto.lower() if ch.isalnum() or ch in " -.").strip(" .-")


def eh_alucinacao(texto: str) -> bool:
    """True se o texto é só pontuação/vazio ou uma frase típica de alucinação."""
    if not any(ch.isalnum() for ch in texto):
        return True
    if len(texto.strip()) <= _CREDITO_MAX_CHARS and _CREDITO.search(texto.strip()):
        return True
    return _normalizar(texto) in ALUCINACOES


class DetectorFundo:
    """Marca falas bem abaixo do volume típico (ex.: dúvida de colega em sala).

    A referência é o percentil 75 móvel do RMS das últimas falas; só passa a
    marcar depois de ``minimo`` amostras, para não errar no começo.
    """

    def __init__(self, janela: int = 40, minimo: int = 5, fator: float = 0.45):
        self.janela, self.minimo, self.fator = janela, minimo, fator
        self.niveis: list[float] = []

    def e_fundo(self, rms: float) -> bool:
        self.niveis.append(rms)
        del self.niveis[:-self.janela]
        ref = float(np.percentile(self.niveis, 75))
        return len(self.niveis) >= self.minimo and rms < self.fator * ref
