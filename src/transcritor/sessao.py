"""Fila + thread de transcrição ao vivo que nunca morre em silêncio."""
from __future__ import annotations

import queue
import threading
from typing import Callable

import numpy as np

from .filtros import DetectorFundo
from .motor import formatar_tempo, processar_trecho


class TrabalhadorTranscricao:
    """Recebe trechos de áudio, transcreve em uma thread e entrega linhas de texto.

    Qualquer exceção em um trecho vira uma linha de erro no texto (o áudio continua
    salvo no .wav) e a thread segue para o próximo trecho.
    """

    def __init__(self, modelo, ao_receber_linha: Callable[[str], None],
                 processar: Callable = processar_trecho):
        self.modelo = modelo
        self.saida = ao_receber_linha
        self.processar = processar
        self.fila: queue.Queue = queue.Queue()
        self.detector = DetectorFundo()
        self.thread = threading.Thread(target=self._rodar, daemon=True)

    def iniciar(self) -> None:
        self.thread.start()

    def enviar(self, t0: float, audio: np.ndarray) -> None:
        self.fila.put((t0, audio))

    def finalizar(self, timeout: float | None = None) -> None:
        """Espera terminar o que falta e encerra a thread."""
        self.fila.put(None)
        self.thread.join(timeout)

    def _rodar(self) -> None:
        while True:
            item = self.fila.get()
            if item is None:
                return
            t0, audio = item
            try:
                for linha in self.processar(self.modelo, audio, t0, self.detector):
                    self.saida(str(linha))
            except Exception as e:  # noqa: BLE001 - a thread não pode morrer
                self.saida(f"[{formatar_tempo(t0)}] [ERRO na transcrição deste trecho: {e!r}] "
                           "(o áudio continua salvo)")
