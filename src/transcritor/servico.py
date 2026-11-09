"""Serviço da interface: estado do modelo, transcrição de arquivos e eventos ao vivo.

Não depende do FastAPI: a camada web só chama estes métodos e repassa os eventos por SSE.
"""
from __future__ import annotations

import shutil
import threading
import time
from pathlib import Path
from typing import Callable

from . import erros, modelos, motor
from .hardware import Hardware, detectar_hardware
from .historico import ARQ_TEXTO, Historico
from .motor import formatar_tempo

Publicar = Callable[[str, dict], None]


class Ocupado(Exception):
    """Já existe uma transcrição em andamento."""


class NaoPronto(Exception):
    """O modelo ainda não está carregado."""


def estimar_restante(decorrido_s: float, progresso: float) -> float | None:
    """Segundos que faltam, ou None enquanto ainda é cedo para estimar."""
    if progresso < 0.02 or decorrido_s < 2.0:
        return None
    return decorrido_s * (1.0 - progresso) / progresso


def descrever_hardware(hw: Hardware) -> dict:
    if hw.usa_gpu:
        nome = hw.gpu.split(",")[0].strip() if hw.gpu else "Nvidia"
        msg = f"Placa de vídeo detectada ({nome}): transcrição rápida."
    else:
        msg = hw.aviso
    return {"usa_gpu": hw.usa_gpu, "gpu": hw.gpu, "modelo": hw.modelo, "ram_gb": round(hw.ram_gb, 1),
            "mensagem": msg, "aviso": bool(hw.aviso)}


class Servico:
    def __init__(self, saidas: Path, hw: Hardware | None = None, cache_dir: str | None = None, *,
                 detectar: Callable[[], Hardware] = detectar_hardware,
                 instalado: Callable = modelos.instalado, baixar: Callable = modelos.baixar,
                 carregar: Callable = motor.carregar_modelo, publicar: Publicar | None = None):
        self.saidas, self.cache_dir = Path(saidas), cache_dir
        self.historico = Historico(self.saidas)
        self._detectar, self._instalado, self._baixar, self._carregar = detectar, instalado, baixar, carregar
        self._hw = hw
        self.publicar: Publicar = publicar or (lambda tipo, dados: None)
        self.lock = threading.RLock()
        self.modelo = None
        self.estado_modelo = {"fase": "verificando", "progresso": 0.0, "nome": "", "tamanho_mb": 0,
                              "baixado_mb": 0.0, "erro": ""}
        self.hardware: dict = {}
        self.trabalho: dict | None = None
        self.linhas: list[dict] = []
        self._parar = threading.Event()
        self._ultimo_pub = 0.0

    # ---------- estado ----------
    def snapshot(self) -> dict:
        with self.lock:
            return {"hardware": dict(self.hardware), "modelo": dict(self.estado_modelo),
                    "trabalho": dict(self.trabalho) if self.trabalho else None,
                    "linhas": list(self.linhas)}

    def _estado(self) -> dict:
        s = self.snapshot()
        s.pop("linhas")
        return s

    def _pub(self) -> None:
        self.publicar("estado", self._estado())

    def _set_modelo(self, **campos) -> None:
        with self.lock:
            self.estado_modelo.update(campos)
        self._pub()

    # ---------- modelo ----------
    def iniciar(self) -> None:
        """Detecta o hardware e prepara o modelo, sem travar a abertura da página."""
        threading.Thread(target=self._verificar, daemon=True).start()

    def _verificar(self) -> None:
        try:
            hw = self._hw or self._detectar()
            with self.lock:
                self._hw = hw
                self.hardware = descrever_hardware(hw)
                self.estado_modelo.update(nome=hw.modelo, tamanho_mb=modelos.tamanho_aproximado_mb(hw.modelo))
            if self._instalado(hw.modelo, self.cache_dir):
                self._carregar_modelo()
            else:
                self._set_modelo(fase="faltando")
        except Exception as e:  # noqa: BLE001
            erros.registrar(self.saidas, e)
            self._set_modelo(fase="erro", erro=erros.MSG_CARREGAR)

    def baixar_modelo(self) -> None:
        with self.lock:
            if self.estado_modelo["fase"] not in ("faltando", "erro") or self._hw is None:
                return
            self.estado_modelo.update(fase="baixando", progresso=0.0, baixado_mb=0.0, erro="")
        self._pub()
        threading.Thread(target=self._baixar_e_carregar, daemon=True).start()

    def _baixar_e_carregar(self) -> None:
        def prog(frac, feito_mb, total_mb):
            self._set_modelo(progresso=round(frac, 4), baixado_mb=round(feito_mb, 1),
                             tamanho_mb=round(total_mb))
        try:
            self._baixar(self._hw.modelo, prog, self.cache_dir)
        except Exception as e:  # noqa: BLE001
            erros.registrar(self.saidas, e)
            self._set_modelo(fase="faltando", erro=erros.MSG_DOWNLOAD)
            return
        self._carregar_modelo()

    def _carregar_modelo(self) -> None:
        self._set_modelo(fase="carregando", erro="")
        try:
            modelo, hw = self._carregar(self._hw, self.cache_dir)
        except Exception as e:  # noqa: BLE001
            erros.registrar(self.saidas, e)
            self._set_modelo(fase="erro", erro=erros.MSG_CARREGAR)
            return
        with self.lock:
            self.modelo, self._hw = modelo, hw
            self.hardware = descrever_hardware(hw)
            self.estado_modelo.update(fase="pronto", progresso=1.0, nome=hw.modelo)
        self._pub()

    # ---------- transcrição de arquivo ----------
    def iniciar_arquivo(self, caminho: str, nome: str, idioma: str | None = "pt",
                        pasta_temp: Path | None = None) -> str:
        with self.lock:
            if self.estado_modelo["fase"] != "pronto" or self.modelo is None:
                raise NaoPronto(erros.MSG_NAO_PRONTO)
            if self.trabalho and self.trabalho["fase"] in ("preparando", "transcrevendo", "parando"):
                raise Ocupado(erros.MSG_OCUPADO)
            id_, pasta = self.historico.nova(nome)
            self._parar = threading.Event()
            self.linhas = []
            self.trabalho = {"id": id_, "nome": nome, "fase": "preparando", "progresso": 0.0,
                             "restante_s": None, "decorrido_s": 0.0, "duracao_s": None, "erro": "",
                             "aviso": ""}
            parar = self._parar
        self.historico.gravar_meta(pasta, {"nome": nome, "tipo": "arquivo", "status": "em_andamento",
                                           "criado": time.strftime("%Y-%m-%d %H:%M:%S"),
                                           "modelo": self._hw.modelo})
        self._pub()
        threading.Thread(target=self._rodar_arquivo, args=(id_, pasta, caminho, idioma, parar, pasta_temp),
                         daemon=True).start()
        return id_

    def parar(self) -> None:
        with self.lock:
            if self.trabalho and self.trabalho["fase"] in ("preparando", "transcrevendo"):
                self._parar.set()
                self.trabalho["fase"] = "parando"
        self._pub()

    def _atualizar_trabalho(self, forcar: bool = False, **campos) -> None:
        with self.lock:
            self.trabalho.update(campos)
            agora = time.time()
            if not forcar and agora - self._ultimo_pub < 0.4:
                return
            self._ultimo_pub = agora
        self._pub()

    def _rodar_arquivo(self, id_, pasta: Path, caminho, idioma, parar, pasta_temp) -> None:
        inicio, t_trans, n = time.time(), [None], 0
        status, erro, duracao = "concluido", "", None

        def progresso(fim: float, dur: float) -> None:
            nonlocal duracao
            duracao = dur
            if t_trans[0] is None:
                t_trans[0] = time.time()
            frac = min(fim / dur, 1.0) if dur else 0.0
            dec = time.time() - t_trans[0]
            with self.lock:
                fase = "parando" if parar.is_set() else "transcrevendo"
            self._atualizar_trabalho(fase=fase, progresso=round(frac, 4), duracao_s=round(dur, 1),
                                     decorrido_s=round(dec, 1), restante_s=estimar_restante(dec, frac))

        try:
            with open(pasta / ARQ_TEXTO, "a", encoding="utf-8") as f:
                for linha in motor.transcrever_arquivo(self.modelo, caminho, idioma, parar, progresso):
                    f.write(str(linha) + "\n")
                    f.flush()
                    n += 1
                    item = {"s": int(linha.inicio), "t": formatar_tempo(linha.inicio),
                            "texto": linha.texto, "fundo": linha.fundo}
                    with self.lock:
                        self.linhas.append(item)
                    self.publicar("linha", item)
            if parar.is_set():
                status = "parado"
        except Exception as e:  # noqa: BLE001 - a thread não pode morrer em silêncio
            status, erro = "erro", erros.traduzir_erro(e)
            erros.registrar(self.saidas, e)
        finally:
            if pasta_temp:
                shutil.rmtree(pasta_temp, ignore_errors=True)
        aviso = ""
        if status == "concluido" and n == 0:
            aviso = "Não encontrei fala nesse arquivo. Confira se ele tem voz e se o volume não está muito baixo."
        meta = {"nome": self.trabalho["nome"], "tipo": "arquivo", "status": status, "linhas": n,
                "criado": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(inicio)),
                "duracao_s": round(duracao, 1) if duracao else None,
                "tempo_s": round(time.time() - inicio, 1), "modelo": self._hw.modelo}
        self.historico.gravar_meta(pasta, meta)
        self._atualizar_trabalho(forcar=True, fase=status, erro=erro, aviso=aviso, restante_s=None,
                                 progresso=1.0 if status == "concluido" else self.trabalho["progresso"])
        self.publicar("historico", {})

    # ---------- texto ----------
    def texto(self, id_: str, horarios: bool = True) -> str | None:
        return self.historico.texto(id_, horarios)
