"""Mensagens de erro em linguagem simples (nada de traceback para o usuário)."""
from __future__ import annotations

import logging
import traceback
from pathlib import Path

from .captura import ErroCaptura
from .motor import ArquivoIlegivel
from .youtube import ErroYoutube

MSG_ARQUIVO = ("Não consegui abrir esse arquivo. Confira se é um áudio ou vídeo comum "
               "(mp3, wav, m4a, mp4...) e se não está corrompido.")
MSG_MEMORIA = ("O computador ficou sem memória para transcrever esse arquivo. "
               "Feche outros programas e tente de novo, ou use um arquivo menor.")
MSG_GENERICA = ("Algo deu errado ao transcrever. Tente de novo; se continuar, "
                "o detalhe técnico ficou salvo no arquivo registro.log, na pasta de transcrições.")
MSG_DOWNLOAD = ("Não consegui baixar o modelo de voz. Verifique a conexão com a internet e "
                "tente de novo — o que já foi baixado não se perde.")
MSG_CARREGAR = ("Não consegui preparar o modelo de voz. Tente baixar de novo; se continuar, "
                "o detalhe técnico ficou salvo no arquivo registro.log.")
MSG_OCUPADO = "Já tem uma transcrição em andamento. Espere terminar ou clique em Parar."
MSG_NAO_PRONTO = "O transcritor ainda está se preparando. Espere um instante."


def registrar(pasta_saidas: Path, exc: BaseException) -> None:
    """Guarda o detalhe técnico em saidas/registro.log (sem mostrar ao usuário)."""
    try:
        pasta_saidas.mkdir(parents=True, exist_ok=True)
        with open(pasta_saidas / "registro.log", "a", encoding="utf-8") as f:
            f.write("".join(traceback.format_exception(exc)) + "\n---\n")
    except Exception:  # registrar nunca pode causar outro erro
        logging.exception("falha ao gravar registro.log")


def traduzir_erro(exc: BaseException) -> str:
    if isinstance(exc, (ErroYoutube, ErroCaptura)):
        return exc.mensagem
    if isinstance(exc, ArquivoIlegivel):
        return MSG_ARQUIVO
    if isinstance(exc, MemoryError) or "out of memory" in str(exc).lower():
        return MSG_MEMORIA
    return MSG_GENERICA
