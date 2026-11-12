"""Gravação ao vivo: medidor de nível, .wav original em blocos e texto por trechos de 30 s.

Duas peças:

- ``Previa``: só mede o volume (antes de gravar). Nada é gravado nem guardado.
- ``Gravacao``: capta, grava o áudio original, manda trechos de 30 s para a transcrição e
  avisa quando a transcrição fica para trás.

Ambas recebem um ``ConjuntoCaptura`` já aberto, então os testes usam fontes falsas.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable

import numpy as np

from .captura import MSG_PERDEU, WAV_BLOCK_S, ConjuntoCaptura, ErroCaptura, GravadorWav, MedidorNivel, misturar
from .motor import TARGET_SR, Linha, processar_trecho
from .sessao import TrabalhadorTranscricao

TRECHO_S = 30            # tamanho de cada trecho enviado ao Whisper
ATRASO_AVISO_S = 90      # a transcrição está "atrasada" se ficar mais que isso atrás da gravação
PASSO_S = 0.1            # de quanto em quanto tempo o áudio é recolhido (e o medidor atualiza)
RESTO_MINIMO_S = 0.5     # sobra final menor que isso nem vale transcrever
PREVIA_OCIOSA_S = 15.0   # a prévia desliga sozinha se a página parar de renová-la

AoNivel = Callable[[str, dict], None]


class Previa:
    """Mede o nível do som sem gravar. Desliga sozinha se ninguém a renovar."""

    def __init__(self, conjunto: ConjuntoCaptura, ao_nivel: AoNivel, chave=None, passo_s: float = PASSO_S,
                 ociosa_s: float = PREVIA_OCIOSA_S, relogio: Callable[[], float] = time.monotonic):
        self.conjunto, self.ao_nivel, self.chave = conjunto, ao_nivel, chave
        self.passo_s, self.ociosa_s, self._relogio = passo_s, ociosa_s, relogio
        self._ultimo = relogio()
        self._fim = threading.Event()
        self.thread = threading.Thread(target=self._rodar, daemon=True)
        self.thread.start()

    @property
    def viva(self) -> bool:
        return self.thread.is_alive() and not self._fim.is_set()

    def renovar(self) -> None:
        self._ultimo = self._relogio()

    def parar(self) -> None:
        self._fim.set()
        if threading.current_thread() is not self.thread:
            self.thread.join(3)

    def _rodar(self) -> None:
        medidores = {c.rotulo: MedidorNivel(self.passo_s) for c in self.conjunto.capturas}
        try:
            while not self._fim.wait(self.passo_s):
                if self._relogio() - self._ultimo > self.ociosa_s:
                    break
                if not all(c.ativa() for c in self.conjunto.capturas):
                    break                                # a captura caiu: a página liga de novo na próxima renovação
                niveis = {}
                for c in self.conjunto.capturas:
                    try:
                        x = c.coletar()          # só para medir; é descartado na hora
                    except Exception:  # noqa: BLE001
                        x = np.zeros(0, np.float32)
                    niveis[c.rotulo] = medidores[c.rotulo].registrar(x)
                self.ao_nivel("previa", niveis)
        finally:
            self._fim.set()
            self.conjunto.fechar()


@dataclass
class Resultado:
    gravado_s: float
    falha: ErroCaptura | None = None


class Gravacao:
    """Uma sessão de gravação. ``executar()`` roda até ``parar`` ser acionado (ou o dispositivo falhar)."""

    def __init__(self, conjunto: ConjuntoCaptura, pasta: str, modelo, parar: threading.Event, *,
                 ao_linha: Callable[[Linha], None], ao_erro: Callable[[float, BaseException], None],
                 ao_nivel: AoNivel, ao_progresso: Callable[[float, float, int], None],
                 processar: Callable = processar_trecho, idioma: str | None = "pt",
                 trecho_s: float = TRECHO_S, bloco_s: float = WAV_BLOCK_S, passo_s: float = PASSO_S):
        self.conjunto, self.pasta, self.parar = conjunto, pasta, parar
        self.ao_nivel, self.ao_progresso = ao_nivel, ao_progresso
        self.trecho_amostras = int(trecho_s * TARGET_SR)
        self.bloco_s, self.passo_s = bloco_s, passo_s
        self.trabalhador = TrabalhadorTranscricao(
            modelo, processar=processar, idioma=idioma, ao_linha=ao_linha, ao_erro=ao_erro, ao_concluir=self._concluiu)
        self.transcrito_ate = 0.0       # segundos de áudio já transcritos
        self.gravado_s = 0.0

    def _concluiu(self, t0: float, duracao: float) -> None:
        self.transcrito_ate = max(self.transcrito_ate, t0 + duracao)

    @property
    def atraso_s(self) -> float:
        return max(0.0, self.gravado_s - self.transcrito_ate)

    def executar(self) -> Resultado:
        gravador = GravadorWav(self.pasta, self.bloco_s)
        medidores = {c.rotulo: MedidorNivel(self.passo_s or PASSO_S) for c in self.conjunto.capturas}
        self.trabalhador.iniciar()
        pendente: list[np.ndarray] = []
        n_pendente, enviado, gravado, falha, ultimo_seg = 0, 0, 0, None, -1
        concluiu_bem = False
        try:
            while True:
                parou = self.parar.wait(self.passo_s)
                pecas, niveis = [], {}
                for c in self.conjunto.capturas:
                    try:
                        if not c.ativa():
                            raise getattr(c, "erro_perda", None) or ErroCaptura(MSG_PERDEU, "perdeu")
                        x = c.coletar()
                    except ErroCaptura as e:
                        falha, x = e, np.zeros(0, np.float32)
                    except Exception:  # noqa: BLE001
                        falha, x = ErroCaptura(MSG_PERDEU, "perdeu"), np.zeros(0, np.float32)
                    pecas.append(x)
                    niveis[c.rotulo] = medidores[c.rotulo].registrar(x)
                mix = misturar(pecas)
                if len(mix):
                    gravador.escrever(mix)
                    pendente.append(mix)
                    n_pendente += len(mix)
                    gravado += len(mix)
                    self.gravado_s = gravado / TARGET_SR
                self.ao_nivel("gravacao", niveis)
                while n_pendente >= self.trecho_amostras:
                    todo = np.concatenate(pendente)
                    self.trabalhador.enviar(enviado / TARGET_SR, todo[:self.trecho_amostras])
                    enviado += self.trecho_amostras
                    resto = todo[self.trecho_amostras:]
                    pendente, n_pendente = ([resto] if len(resto) else []), len(resto)
                if int(self.gravado_s) != ultimo_seg:
                    ultimo_seg = int(self.gravado_s)
                    self.ao_progresso(self.gravado_s, self.atraso_s, self.trabalhador.pendentes)
                if parou or falha:
                    break
            if n_pendente >= RESTO_MINIMO_S * TARGET_SR:      # a sobra final também é transcrita
                self.trabalhador.enviar(enviado / TARGET_SR, np.concatenate(pendente))
            concluiu_bem = True
        finally:
            self.conjunto.fechar()          # o microfone desliga assim que a gravação termina
            gravador.fechar()
            if not concluiu_bem:            # erro inesperado: não deixa a thread de transcrição presa
                self.trabalhador.encerrar()
        self.trabalhador.encerrar()
        self.ao_progresso(self.gravado_s, self.atraso_s, self.trabalhador.pendentes)
        while self.trabalhador.vivo:        # espera a transcrição alcançar, mostrando o que falta
            self.trabalhador.esperar(1.0)
            self.ao_progresso(self.gravado_s, self.atraso_s, self.trabalhador.pendentes)
        return Resultado(self.gravado_s, falha)
