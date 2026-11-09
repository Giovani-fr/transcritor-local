"""Formatos de texto da transcrição: com horários, em parágrafos e leitura do .txt salvo."""
from __future__ import annotations

import re

from .motor import formatar_tempo

_LINHA = re.compile(r"^\[(\d+):(\d\d):(\d\d)\](?: \(fundo\))? ?(.*)$")


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


def segmentos_do_txt(conteudo: str) -> list[tuple[float, str]]:
    """Lê linhas '[hh:mm:ss] texto' de volta para (segundos, texto). Ignora linhas fora do padrão."""
    saida = []
    for linha in conteudo.splitlines():
        m = _LINHA.match(linha.strip())
        if m:
            h, mi, se, txt = m.groups()
            saida.append((int(h) * 3600 + int(mi) * 60 + int(se), txt))
    return saida


def versao_do_txt(conteudo: str, horarios: bool) -> str:
    segs = segmentos_do_txt(conteudo)
    return formatar_com_horarios(segs) if horarios else texto_corrido(segs)
