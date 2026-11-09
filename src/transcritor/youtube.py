"""YouTube: legendas (modo rápido) e utilitários de texto.

O modo "preciso" (baixar o áudio com yt-dlp e transcrever com Whisper) entra na Fase 3.
"""
from __future__ import annotations

import re

from .texto import formatar_com_horarios, texto_corrido  # noqa: F401  (reexportados)

_ID = re.compile(r"(?:v=|youtu\.be/|/shorts/|/embed/|/live/)([\w-]{11})")


class LinkInvalido(ValueError):
    """O texto colado não parece um link do YouTube."""


def extrair_id(url: str) -> str:
    m = _ID.search(url.strip())
    if m:
        return m.group(1)
    if re.fullmatch(r"[\w-]{11}", url.strip()):
        return url.strip()
    raise LinkInvalido("Esse não parece um link do YouTube. Cole o endereço completo do vídeo.")


def buscar_legendas(url: str) -> tuple[list[tuple[float, str]], str]:
    """Devolve ([(segundos, texto)], idioma). Prefere português; senão, a primeira disponível."""
    from youtube_transcript_api import YouTubeTranscriptApi
    lista = YouTubeTranscriptApi().list(extrair_id(url))
    try:
        tr = lista.find_transcript(["pt", "pt-BR"])
    except Exception:
        tr = next(iter(lista))
    dados = tr.fetch()
    return [(sn.start, sn.text.replace("\n", " ").strip()) for sn in dados], tr.language_code
