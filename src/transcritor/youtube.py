"""YouTube: legendas (modo rápido) e utilitários de texto.

O modo "preciso" (baixar o áudio com yt-dlp e transcrever com Whisper) entra na Fase 3.
"""
from __future__ import annotations

import re

from .motor import formatar_tempo

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


def formatar_com_horarios(segmentos: list[tuple[float, str]]) -> str:
    return "\n".join(f"[{formatar_tempo(t)}] {txt}" for t, txt in segmentos)


def texto_corrido(segmentos: list[tuple[float, str]], frases_por_paragrafo: int = 5) -> str:
    """Junta os trechos em parágrafos, quebrando a cada N frases."""
    texto = " ".join(t.strip() for _, t in segmentos if t.strip())
    texto = re.sub(r"\s+", " ", texto)
    frases = re.split(r"(?<=[.!?])\s+", texto)
    paragrafos = [" ".join(frases[i:i + frases_por_paragrafo])
                  for i in range(0, len(frases), frases_por_paragrafo)]
    return "\n\n".join(p for p in paragrafos if p)
