"""Fila + thread de transcrição ao vivo que nunca morre em silêncio."""
from __future__ import annotations

import queue
import threading
from typing import Callable

import numpy as np

from .filtros import DetectorFundo
from .motor import TARGET_SR, Linha, formatar_tempo, processar_trecho


class TrabalhadorTranscricao:
    """Recebe trechos de áudio, transcreve em uma thread e entrega as linhas de texto.

    Qualquer exceção em um trecho vira um aviso no texto (o áudio continua salvo no .wav)
    e a thread segue para o próximo trecho.

    Por padrão entrega texto pronto por ``ao_receber_linha``. Quem precisa de mais controle
    passa ``ao_linha`` (recebe a ``Linha``, com a marca de fundo), ``ao_erro`` (recebe o
    horário e a exceção) e ``ao_concluir`` (horário e duração de cada trecho já processado).
    """

    def __init__(self, modelo, ao_receber_linha: Callable[[str], None] | None = None,
                 processar: Callable = processar_trecho, *,
                 idioma: str | None = "pt",
                 ao_linha: Callable[[Linha], None] | None = None,
                 ao_erro: Callable[[float, BaseException], None] | None = None,
                 ao_concluir: Callable[[float, float], None] | None = None):
        self.modelo = modelo
        self.saida = ao_receber_linha or (lambda texto: None)
        self.ao_linha, self.ao_erro, self.ao_concluir = ao_linha, ao_erro, ao_concluir
        self.processar = processar
        self.idioma = idioma
        self.fila: queue.Queue = queue.Queue()
        self.detector = DetectorFundo()
        self.thread = threading.Thread(target=self._rodar, daemon=True)
        self._pendentes = 0
        self._lock = threading.Lock()

    @property
    def pendentes(self) -> int:
        """Trechos que ainda não terminaram (inclui o que está sendo transcrito agora)."""
        with self._lock:
            return self._pendentes

    @property
    def vivo(self) -> bool:
        return self.thread.is_alive()

    def iniciar(self) -> None:
        self.thread.start()

    def enviar(self, t0: float, audio: np.ndarray) -> None:
        with self._lock:
            self._pendentes += 1
        self.fila.put((t0, audio))

    def encerrar(self) -> None:
        """Avisa que não vêm mais trechos; a thread termina o que falta e para."""
        self.fila.put(None)

    def esperar(self, timeout: float | None = None) -> None:
        self.thread.join(timeout)

    def finalizar(self, timeout: float | None = None) -> None:
        """Espera terminar o que falta e encerra a thread."""
        self.encerrar()
        self.esperar(timeout)

    def _rodar(self) -> None:
        while True:
            item = self.fila.get()
            if item is None:
                return
            t0, audio = item
            try:
                for linha in self.processar(self.modelo, audio, t0, self.detector, idioma=self.idioma):
                    if self.ao_linha:
                        self.ao_linha(linha)
                    else:
                        self.saida(str(linha))
            except Exception as e:  # noqa: BLE001 - a thread não pode morrer
                try:
                    if self.ao_erro:
                        self.ao_erro(t0, e)
                    else:
                        self.saida(f"[{formatar_tempo(t0)}] [ERRO na transcrição deste trecho: {e!r}] "
                                   "(o áudio continua salvo)")
                except Exception:  # noqa: BLE001
                    pass
            finally:
                with self._lock:
                    self._pendentes -= 1
                if self.ao_concluir:
                    try:
                        self.ao_concluir(t0, len(audio) / TARGET_SR)
                    except Exception:  # noqa: BLE001
                        pass
