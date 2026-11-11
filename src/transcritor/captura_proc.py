"""Captura de áudio em um processo separado.

O PortAudio é código nativo: se ele travar (access violation), derruba o processo inteiro. Por isso o
microfone e a listagem de dispositivos ficam num processo filho. Se ele morrer, o servidor continua
no ar, avisa em linguagem simples e abre outro processo na próxima vez.

Protocolo (tuplas pelo ``multiprocessing.Pipe``):

    servidor -> filho:  ("cmd", id, nome, args)   nomes: microfones | abrir | fechar | sair
    filho -> servidor:  ("resp", id, ok, valor)
                        ("audio", sessao, rotulo, bytes float32 16 kHz mono)
                        ("perdeu", sessao, rotulo)      # o dispositivo parou de responder
"""
from __future__ import annotations

import itertools
import multiprocessing
import signal
import threading
import time
import traceback
from pathlib import Path
from typing import Callable

import numpy as np

from .captura import MSG_CAPTURA, MSG_PERDEU, MSG_PROCESSO, ConjuntoCaptura, DispositivosWasapi, ErroCaptura

PASSO_FILHO_S = 0.05        # de quanto em quanto tempo o filho entrega o áudio acumulado
TIMEOUT_CHAMADA_S = 20.0    # um pedido que passa disso é tratado como travamento
VALIDADE_LISTA_S = 5.0      # a lista de dispositivos fica em cache por alguns segundos


# ====================== lado do filho ======================
def principal_filho(conn, fabrica, saidas: str | None) -> None:
    """Corpo do processo de captura. ``fabrica()`` devolve o objeto de dispositivos (Wasapi ou falso).

    O Ctrl+C do console chega a TODOS os processos da janela. O filho o ignora: ele só termina quando o
    servidor pede ("sair") ou morre (o pipe fecha), sem imprimir traceback.
    """
    for nome in ("SIGINT", "SIGBREAK"):
        if hasattr(signal, nome):
            signal.signal(getattr(signal, nome), signal.SIG_IGN)
    if saidas:
        from . import diagnostico
        diagnostico.ativar(Path(saidas), "crash-captura.log")
    backend = fabrica()
    sessoes: dict[int, ConjuntoCaptura] = {}
    avisadas: set[tuple[int, str]] = set()
    try:
        while True:
            try:
                while conn.poll(PASSO_FILHO_S):
                    msg = conn.recv()
                    if msg[0] == "cmd" and msg[2] == "sair":
                        return
                    _tratar(conn, backend, sessoes, msg)
                for sid, conjunto in list(sessoes.items()):
                    for c in conjunto.capturas:
                        x = c.coletar()
                        if len(x):
                            conn.send(("audio", sid, c.rotulo, np.asarray(x, np.float32).tobytes()))
                        if (sid, c.rotulo) not in avisadas and not c.ativa():
                            avisadas.add((sid, c.rotulo))
                            conn.send(("perdeu", sid, c.rotulo))
            except (EOFError, OSError, KeyboardInterrupt):   # o servidor foi embora: não deixa processo órfão
                return
    finally:
        for conjunto in sessoes.values():
            conjunto.fechar()
        if hasattr(backend, "encerrar"):
            backend.encerrar()


def _tratar(conn, backend, sessoes: dict, msg: tuple) -> None:
    _, id_, nome, args = msg
    try:
        if nome == "microfones":
            conn.send(("resp", id_, True, backend.microfones()))
        elif nome == "abrir":
            sid, fonte, microfone = args
            conjunto = backend.abrir(fonte, microfone)
            sessoes[sid] = conjunto
            conn.send(("resp", id_, True, [c.rotulo for c in conjunto.capturas]))
        elif nome == "sinais":                      # só para testes: o Ctrl+C está ignorado aqui?
            conn.send(("resp", id_, True, {n: signal.getsignal(getattr(signal, n)) == signal.SIG_IGN
                                           for n in ("SIGINT", "SIGBREAK") if hasattr(signal, n)}))
        elif nome == "fechar":
            conjunto = sessoes.pop(args[0], None)
            if conjunto:
                conjunto.fechar()
            conn.send(("resp", id_, True, None))
        else:
            conn.send(("resp", id_, False, ("excecao", f"comando desconhecido: {nome}")))
    except ErroCaptura as e:
        conn.send(("resp", id_, False, ("captura", e.mensagem, e.codigo)))
    except (EOFError, ConnectionError):          # o servidor sumiu: o laço principal encerra
        raise
    except Exception:  # noqa: BLE001
        conn.send(("resp", id_, False, ("excecao", traceback.format_exc())))


# ====================== lado do servidor ======================
class CapturaRemota:
    """Uma fonte (mic ou sistema) cujo áudio chega do processo filho. Mesma interface de ``Captura``."""

    def __init__(self, rotulo: str):
        self.rotulo = rotulo
        self._buf: list[np.ndarray] = []
        self._lock = threading.Lock()
        self.erro_perda: ErroCaptura | None = None

    def _receber(self, dados: bytes) -> None:
        x = np.frombuffer(dados, dtype=np.float32)
        with self._lock:
            self._buf.append(x)

    def coletar(self) -> np.ndarray:
        with self._lock:
            out = np.concatenate(self._buf) if self._buf else np.zeros(0, np.float32)
            self._buf = []
        return out

    def ativa(self) -> bool:
        return self.erro_perda is None

    def fechar(self) -> None:      # quem fecha de verdade é o ConjuntoCaptura (avisa o processo filho)
        pass


class _Espera:
    def __init__(self, tratar: Callable | None = None):
        self.evento = threading.Event()
        self.ok, self.valor, self.tratar = False, None, tratar


class ProcessoCaptura:
    """Dono do processo filho: abre quando preciso, vigia a morte dele e entrega o áudio às fontes."""

    def __init__(self, fabrica=DispositivosWasapi, saidas: Path | str | None = None,
                 timeout_s: float = TIMEOUT_CHAMADA_S, ao_morrer: Callable[[int | None, bool], None] | None = None):
        self.fabrica, self.saidas, self.timeout_s = fabrica, saidas, timeout_s
        self.ao_morrer = ao_morrer
        self._ctx = multiprocessing.get_context("spawn")
        self._lock = threading.RLock()          # abrir/enviar/encerrar o filho
        self._lock_estado = threading.Lock()    # tabelas de pedidos e sessões (a thread leitora usa só esta)
        self._ids = itertools.count(1)
        self._sids = itertools.count(1)
        self._proc = None
        self._conn = None
        self._geracao = 0
        self._pendentes: dict[int, _Espera] = {}
        self._sessoes: dict[int, dict[str, CapturaRemota]] = {}
        self.inicios = 0            # quantas vezes o filho foi aberto (testes)

    @property
    def viva(self) -> bool:
        return self._proc is not None and self._proc.is_alive()

    # ---- ciclo de vida ----
    def _garantir(self) -> None:
        if self._proc is not None and self._proc.is_alive():
            return
        if self._proc is not None:                 # morreu e ninguém reparou ainda
            self._morreu(self._geracao)
        pai, filho = self._ctx.Pipe(duplex=True)
        proc = self._ctx.Process(target=principal_filho, args=(filho, self.fabrica,
                                                               str(self.saidas) if self.saidas else None),
                                 daemon=True)
        proc.start()
        filho.close()           # sem isso o servidor nunca perceberia o fim do filho
        self._geracao += 1
        self._proc, self._conn = proc, pai
        self.inicios += 1
        threading.Thread(target=self._ler, args=(pai, proc, self._geracao), daemon=True).start()

    def _ler(self, conn, proc, geracao: int) -> None:
        try:
            while True:
                msg = conn.recv()
                self._despachar(msg)
        except (EOFError, OSError):
            pass
        except Exception:  # noqa: BLE001 - mensagem ilegível: trata como processo perdido
            pass
        finally:
            self._morreu(geracao, proc)

    def _despachar(self, msg: tuple) -> None:
        tipo = msg[0]
        if tipo == "resp":
            _, id_, ok, valor = msg
            with self._lock_estado:
                espera = self._pendentes.pop(id_, None)
            if espera is None:
                return
            if ok and espera.tratar:
                valor = espera.tratar(valor)      # roda aqui, antes de o áudio dessa sessão ser lido
            espera.ok, espera.valor = ok, valor
            espera.evento.set()
        elif tipo == "audio":
            _, sid, rotulo, dados = msg
            fonte = self._sessoes.get(sid, {}).get(rotulo)
            if fonte:
                fonte._receber(dados)
        elif tipo == "perdeu":
            _, sid, rotulo = msg
            fonte = self._sessoes.get(sid, {}).get(rotulo)
            if fonte:
                fonte.erro_perda = ErroCaptura(MSG_PERDEU, "perdeu")

    def _morreu(self, geracao: int, proc=None) -> None:
        with self._lock:
            if geracao != self._geracao or self._proc is None:
                return                              # já foi tratado (ou foi substituído por outro filho)
            proc = self._proc
            self._proc, self._conn = None, None
            with self._lock_estado:
                tinha_sessao = bool(self._sessoes)
                pendentes, self._pendentes = self._pendentes, {}
                sessoes, self._sessoes = self._sessoes, {}
        try:
            proc.join(1.0)
        except Exception:  # noqa: BLE001
            pass
        codigo = proc.exitcode
        for fontes in sessoes.values():
            for f in fontes.values():
                f.erro_perda = ErroCaptura(MSG_PROCESSO, "processo_morreu")
        for espera in pendentes.values():
            espera.ok, espera.valor = False, ("captura", MSG_PROCESSO, "processo_morreu")
            espera.evento.set()
        if self.ao_morrer:
            try:
                self.ao_morrer(codigo, tinha_sessao)
            except Exception:  # noqa: BLE001
                pass

    def _matar(self) -> None:
        with self._lock:
            proc = self._proc
        if proc is not None:
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass

    # ---- chamadas ----
    def chamar(self, nome: str, *args, tratar: Callable | None = None):
        with self._lock:
            self._garantir()
            id_ = next(self._ids)
            espera = _Espera(tratar)
            with self._lock_estado:
                self._pendentes[id_] = espera
            try:
                self._conn.send(("cmd", id_, nome, args))
            except (OSError, ValueError):
                with self._lock_estado:
                    self._pendentes.pop(id_, None)
                self._matar()
                raise ErroCaptura(MSG_PROCESSO, "processo_morreu")
        if not espera.evento.wait(self.timeout_s):         # o filho travou sem morrer
            with self._lock_estado:
                self._pendentes.pop(id_, None)
            self._matar()
            raise ErroCaptura(MSG_PROCESSO, "processo_morreu")
        if not espera.ok:
            self._levantar(espera.valor)
        return espera.valor

    @staticmethod
    def _levantar(erro: tuple):
        if erro[0] == "captura":
            raise ErroCaptura(erro[1], erro[2])
        raise _ErroNoFilho(erro[1])

    def abrir(self, fonte: str, microfone: str | None) -> ConjuntoCaptura:
        sid = next(self._sids)

        def registrar(rotulos: list[str]) -> list[CapturaRemota]:
            remotas = [CapturaRemota(r) for r in rotulos]
            with self._lock_estado:
                self._sessoes[sid] = {c.rotulo: c for c in remotas}
            return remotas

        remotas = self.chamar("abrir", sid, fonte, microfone, tratar=registrar)
        return ConjuntoCaptura(remotas, lambda: self.fechar_sessao(sid))

    def fechar_sessao(self, sid: int) -> None:
        with self._lock:
            with self._lock_estado:
                existia = self._sessoes.pop(sid, None) is not None
            if not existia or self._conn is None:
                return
            try:
                self._conn.send(("cmd", next(self._ids), "fechar", (sid,)))
            except (OSError, ValueError):
                pass

    def encerrar(self) -> None:
        """Ao fechar o programa: pede para o filho sair e, se não sair logo, o encerra."""
        with self._lock:
            proc, conn = self._proc, self._conn
            self._geracao += 1                     # a saída esperada não conta como "morreu"
            self._proc, self._conn = None, None
            with self._lock_estado:
                self._sessoes = {}
        if proc is None:
            return
        try:
            conn.send(("cmd", 0, "sair", ()))
        except (OSError, ValueError):
            pass
        proc.join(3)
        if proc.is_alive():
            proc.kill()
            proc.join(2)


class _ErroNoFilho(Exception):
    """Erro inesperado dentro do processo de captura (traceback em texto)."""


class DispositivosIsolados:
    """Mesma interface de ``DispositivosWasapi``, mas tudo roda no processo filho."""

    def __init__(self, fabrica=DispositivosWasapi, saidas: Path | str | None = None,
                 validade_s: float = VALIDADE_LISTA_S, relogio=time.monotonic,
                 timeout_s: float = TIMEOUT_CHAMADA_S):
        self._leve = fabrica()          # só para ``suportado`` (não abre o PortAudio)
        self._validade_s, self._relogio = validade_s, relogio
        self._lock_lista = threading.Lock()
        self._lista: tuple[float, list[dict]] | None = None
        self.ao_morrer: Callable[[int | None, bool], None] | None = None
        self.processo = ProcessoCaptura(fabrica, saidas, timeout_s, ao_morrer=self._morreu)

    def _morreu(self, codigo, tinha_sessao: bool) -> None:
        self._lista = None
        if self.ao_morrer:
            self.ao_morrer(codigo, tinha_sessao)

    def suportado(self) -> tuple[bool, str]:
        return self._leve.suportado()

    def microfones(self) -> list[dict]:
        with self._lock_lista:                     # chamadas simultâneas esperam e reaproveitam a lista
            agora = self._relogio()
            if self._lista and agora - self._lista[0] < self._validade_s:
                return [dict(m) for m in self._lista[1]]
            lista = self.processo.chamar("microfones")
            self._lista = (agora, lista)
            return [dict(m) for m in lista]

    def abrir(self, fonte: str, microfone: str | None = None) -> ConjuntoCaptura:
        ok, motivo = self.suportado()
        if not ok:
            raise ErroCaptura(motivo, "nao_suportado")
        try:
            return self.processo.abrir(fonte, microfone)
        except _ErroNoFilho as e:
            self._lista = None
            raise ErroCaptura(MSG_CAPTURA, "captura") from e

    def encerrar(self) -> None:
        self.processo.encerrar()
