"""Serviço da interface: estado do modelo, transcrições (arquivo e YouTube) e eventos ao vivo.

Não depende do FastAPI: a camada web só chama estes métodos e repassa os eventos por SSE.
"""
from __future__ import annotations

import shutil
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable

from . import erros, modelos, motor
from .captura import MSG_CAPTURA, DispositivosWasapi, ErroCaptura
from .gravacao import ATRASO_AVISO_S, Gravacao, Previa
from .hardware import AVISO_CPU_CURTO, Hardware, detectar_hardware
from .historico import ARQ_TEXTO, Historico
from .motor import Linha, formatar_tempo
from .youtube import ErroYoutube, ProvedorYoutube, atualizar_ytdlp, escolher_legenda, extrair_id, versao_ytdlp

Publicar = Callable[[str, dict], None]
FASES_ATIVAS = ("preparando", "baixando", "transcrevendo", "gravando", "parando")
FONTES = {"reuniao": "reunião", "ambiente": "ambiente"}


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
        msg, detalhe = f"Placa de vídeo detectada ({nome}): transcrição rápida.", ""
    else:
        msg, detalhe = AVISO_CPU_CURTO, hw.aviso
    return {"usa_gpu": hw.usa_gpu, "gpu": hw.gpu, "modelo": hw.modelo, "ram_gb": round(hw.ram_gb, 1),
            "mensagem": msg, "detalhe": detalhe, "aviso": bool(hw.aviso)}


class Servico:
    def __init__(self, saidas: Path, hw: Hardware | None = None, cache_dir: str | None = None, *,
                 detectar: Callable[[], Hardware] = detectar_hardware,
                 instalado: Callable = modelos.instalado, baixar: Callable = modelos.baixar,
                 carregar: Callable = motor.carregar_modelo, publicar: Publicar | None = None,
                 youtube: ProvedorYoutube | None = None, dispositivos=None,
                 atualizar_ytdlp: Callable[[], dict] = atualizar_ytdlp,
                 versao_ytdlp: Callable[[], str] = versao_ytdlp):
        self.saidas, self.cache_dir = Path(saidas), cache_dir
        self.historico = Historico(self.saidas)
        self._detectar, self._instalado, self._baixar, self._carregar = detectar, instalado, baixar, carregar
        self.yt = youtube or ProvedorYoutube()
        self.dispositivos = dispositivos or DispositivosWasapi()
        self._atualizar_ytdlp, self._versao_ytdlp = atualizar_ytdlp, versao_ytdlp
        self._atualizando = False
        self._previa: Previa | None = None
        self._lock_previa = threading.Lock()
        self._info_cache: dict[str, dict] = {}     # evita consultar o YouTube de novo ao clicar em Transcrever
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
        self._pub_agendado = False
        self.intervalo_pub = 0.4      # segundos entre atualizações de progresso (testes usam 0)

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

    # ---------- trabalhos (comum a arquivo e YouTube) ----------
    def _comecar(self, tipo: str, nome: str, extra: dict) -> tuple[str, Path, threading.Event]:
        with self.lock:
            if self.estado_modelo["fase"] != "pronto" or self.modelo is None:
                raise NaoPronto(erros.MSG_NAO_PRONTO)
            if self._ocupado():
                raise Ocupado(erros.MSG_OCUPADO)
            id_, pasta = self.historico.nova(nome)
            self._parar = threading.Event()
            self.linhas = []
            self.trabalho = {"id": id_, "tipo": tipo, "nome": nome, "fase": "preparando", "progresso": 0.0,
                             "restante_s": None, "decorrido_s": 0.0, "duracao_s": None, "erro": "",
                             "aviso": "", "codigo_erro": "", "ajuda": None, "url": "", "modo": "",
                             "canal": "", "legenda": "", "audio_guardado": False, "fonte": "", "gravado_s": 0.0,
                             "atraso_s": 0.0, "atrasado": False, "pendentes": 0, **extra}
            parar = self._parar
        self.historico.gravar_meta(pasta, {"nome": nome, "tipo": tipo, "status": "em_andamento",
                                           "criado": time.strftime("%Y-%m-%d %H:%M:%S"),
                                           "modelo": self._hw.modelo, **self._meta_extra()})
        self._pub()
        return id_, pasta, parar

    def _ocupado(self) -> bool:
        return bool(self._atualizando or (self.trabalho and self.trabalho["fase"] in FASES_ATIVAS))

    def _meta_extra(self) -> dict:
        t = self.trabalho or {}
        return {k: t[k] for k in ("url", "modo", "canal", "fonte") if t.get(k)}

    def parar(self) -> None:
        with self.lock:
            if self.trabalho and self.trabalho["fase"] in ("preparando", "baixando", "transcrevendo", "gravando"):
                self._parar.set()
                self.trabalho["fase"] = "parando"
        self._pub()

    def _atualizar_trabalho(self, forcar: bool = False, **campos) -> None:
        with self.lock:
            mudou_fase = "fase" in campos and campos["fase"] != self.trabalho["fase"]
            self.trabalho.update(campos)
            agora = time.time()
            if not (forcar or mudou_fase) and agora - self._ultimo_pub < self.intervalo_pub:
                if not self._pub_agendado:      # não perde a última atualização de uma rajada
                    self._pub_agendado = True
                    timer = threading.Timer(self.intervalo_pub - (agora - self._ultimo_pub) + 0.01, self._publicar_agendado)
                    timer.daemon = True
                    timer.start()
                return
            self._ultimo_pub = agora
        self._pub()

    def _publicar_agendado(self) -> None:
        with self.lock:
            self._pub_agendado = False
            self._ultimo_pub = time.time()
        self._pub()

    def _registrar_linha(self, arquivo, linha: Linha) -> None:
        arquivo.write(str(linha) + "\n")
        arquivo.flush()
        item = {"s": int(linha.inicio), "t": formatar_tempo(linha.inicio), "texto": linha.texto,
                "fundo": linha.fundo}
        with self.lock:
            self.linhas.append(item)
        self.publicar("linha", item)

    def _transcrever(self, caminho: str, idioma: str | None, parar: threading.Event,
                     pasta: Path) -> tuple[int, float | None]:
        """Transcreve um arquivo de áudio/vídeo com progresso. Devolve (nº de linhas, duração)."""
        t_trans, n, duracao = [None], 0, [None]
        self._atualizar_trabalho(forcar=True, fase="parando" if parar.is_set() else "preparando",
                                 progresso=0.0, restante_s=None)

        def progresso(fim: float, dur: float) -> None:
            duracao[0] = dur
            if t_trans[0] is None:
                t_trans[0] = time.time()
            frac = min(fim / dur, 1.0) if dur else 0.0
            dec = time.time() - t_trans[0]
            fase = "parando" if parar.is_set() else "transcrevendo"
            self._atualizar_trabalho(fase=fase, progresso=round(frac, 4), duracao_s=round(dur, 1),
                                     decorrido_s=round(dec, 1), restante_s=estimar_restante(dec, frac))

        with open(pasta / ARQ_TEXTO, "a", encoding="utf-8") as f:
            for linha in motor.transcrever_arquivo(self.modelo, caminho, idioma, parar, progresso):
                self._registrar_linha(f, linha)
                n += 1
        return n, duracao[0]

    def _executar(self, id_: str, pasta: Path, parar: threading.Event, fazer: Callable[[], tuple[int, float | None]],
                  limpar: Callable[[], None] | None = None, parada_normal: bool = False) -> None:
        """Roda o trabalho numa thread que nunca morre em silêncio e grava o resultado.

        ``parada_normal``: parar é o jeito normal de terminar (gravação ao vivo), não um corte no meio.
        """
        inicio = time.time()
        status, erro, codigo, ajuda, n, duracao = "concluido", "", "", None, 0, None
        try:
            n, duracao = fazer()
            if parar.is_set() and not parada_normal:
                status = "parado"
        except ErroYoutube as e:
            if e.codigo == "cancelado":
                status = "parado"
            else:
                status, erro, codigo, ajuda = "erro", e.mensagem, e.codigo, e.ajuda
                erros.registrar(self.saidas, e)
        except Exception as e:  # noqa: BLE001
            status, erro = "erro", erros.traduzir_erro(e)
            erros.registrar(self.saidas, e)
        finally:
            if limpar:
                limpar()
        with self.lock:
            n = n or len(self.linhas)
            t = dict(self.trabalho)
        aviso = t.get("aviso_fim") or ""
        gravacao = t["tipo"] == "gravacao"
        if aviso:
            pass
        elif status == "concluido" and n == 0 and gravacao:
            aviso = ("Não encontrei fala na gravação. Confira se o volume do microfone está bom na próxima vez. "
                     "O áudio ficou salvo na pasta.")
        elif status == "concluido" and n == 0:
            aviso = "Não encontrei fala nesse arquivo. Confira se ele tem voz e se o volume não está muito baixo."
        elif status == "parado" and n == 0:
            aviso = "Você parou antes de aparecer algum texto."
        elif status == "parado":
            aviso = "Você parou no meio: este é o texto que ficou pronto até aqui."
        if status in ("erro", "parado") and n == 0 and not gravacao:
            shutil.rmtree(pasta, ignore_errors=True)          # sem texto não há o que guardar; erros ficam só no registro.log
        else:                                                 # gravação: o áudio original nunca é apagado
            meta = {"nome": t["nome"], "tipo": t["tipo"],
                    "status": "interrompido" if (gravacao and status == "erro") else status, "linhas": n,
                    "criado": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(inicio)),
                    "duracao_s": round(duracao, 1) if duracao else (t.get("duracao_s") or None),
                    "tempo_s": round(time.time() - inicio, 1), "modelo": self._hw.modelo,
                    **{k: t[k] for k in ("url", "modo", "canal", "legenda", "fonte") if t.get(k)},
                    "audio_guardado": bool(t.get("audio_guardado"))}
            self.historico.gravar_meta(pasta, meta)
        self._atualizar_trabalho(forcar=True, fase=status, erro=erro, aviso=aviso, restante_s=None,
                                 codigo_erro=codigo, ajuda=ajuda,
                                 progresso=1.0 if status == "concluido" else t["progresso"])
        self.publicar("historico", {})

    # ---------- arquivo ----------
    def iniciar_arquivo(self, caminho: str, nome: str, idioma: str | None = "pt",
                        pasta_temp: Path | None = None) -> str:
        id_, pasta, parar = self._comecar("arquivo", nome, {})
        limpar = (lambda: shutil.rmtree(pasta_temp, ignore_errors=True)) if pasta_temp else None
        threading.Thread(target=self._executar, daemon=True, args=(
            id_, pasta, parar, lambda: self._transcrever(caminho, idioma, parar, pasta), limpar)).start()
        return id_

    # ---------- YouTube ----------
    def consultar_youtube(self, url: str) -> dict:
        """Título, duração e se há legendas, antes de transcrever. Levanta ErroYoutube."""
        info = self.yt.info(url)
        self._info_cache[info["id"]] = dict(info)
        try:
            opcoes = self.yt.listar_legendas(info["id"])
        except ErroYoutube as e:
            if e.codigo in ("idade", "privado", "indisponivel", "link_invalido"):
                raise
            opcoes = None          # não deu para saber; o modo Rápido tenta e avisa se falhar
        escolhida = escolher_legenda(opcoes) if opcoes else None
        info["legendas"] = {"disponivel": None if opcoes is None else bool(opcoes),
                            "idioma": escolhida["idioma"] if escolhida else "",
                            "automatica": bool(escolhida and escolhida["automatica"])}
        return info

    def iniciar_youtube(self, url: str, modo: str = "rapido", manter_audio: bool = False,
                        idioma: str | None = "pt") -> str:
        info = self._info_cache.get(extrair_id(url)) or self.yt.info(url)
        id_, pasta, parar = self._comecar("youtube", info["titulo"], {
            "url": info["url"], "modo": modo, "canal": info["canal"], "duracao_s": info["duracao_s"] or None})
        tmp = Path(tempfile.mkdtemp(prefix="transcritor-yt-"))
        if modo == "rapido":
            fazer = lambda: self._fazer_rapido(info, pasta, parar)  # noqa: E731
        else:
            fazer = lambda: self._fazer_preciso(info, pasta, parar, idioma, manter_audio, tmp)  # noqa: E731
        threading.Thread(target=self._executar, daemon=True, args=(
            id_, pasta, parar, fazer, lambda: shutil.rmtree(tmp, ignore_errors=True))).start()
        return id_

    def _fazer_rapido(self, info: dict, pasta: Path, parar: threading.Event) -> tuple[int, float | None]:
        self._atualizar_trabalho(forcar=True, fase="preparando")
        segmentos, idioma, automatica = self.yt.legendas(info["id"])
        rotulo = f"{idioma} ({'automática' if automatica else 'manual'})"
        self._atualizar_trabalho(forcar=True, legenda=rotulo)
        n = 0
        with open(pasta / ARQ_TEXTO, "a", encoding="utf-8") as f:
            for inicio, texto in segmentos:
                if parar.is_set():
                    break
                if texto:
                    self._registrar_linha(f, Linha(inicio, texto))
                    n += 1
        return n, info["duracao_s"] or None

    def _fazer_preciso(self, info: dict, pasta: Path, parar: threading.Event, idioma: str | None,
                       manter: bool, tmp: Path) -> tuple[int, float | None]:
        def baixando(frac: float, eta: float | None) -> None:
            self._atualizar_trabalho(fase="parando" if parar.is_set() else "baixando",
                                     progresso=round(frac, 4), restante_s=eta)

        self._atualizar_trabalho(forcar=True, fase="baixando", progresso=0.0, restante_s=None)
        caminho = self.yt.baixar_audio(info["url"], tmp, baixando, parar)
        if manter:
            shutil.copy2(caminho, pasta / ("audio" + Path(caminho).suffix))
            self._atualizar_trabalho(forcar=True, audio_guardado=True)
        return self._transcrever(str(caminho), idioma, parar, pasta)

    # ---------- gravação ao vivo ----------
    def info_gravacao(self) -> dict:
        """O que a aba Gravar precisa saber: se funciona neste sistema e quais microfones existem."""
        ok, motivo = self.dispositivos.suportado()
        microfones = []
        if ok:
            try:
                microfones = self.dispositivos.microfones()
            except Exception as e:  # noqa: BLE001
                erros.registrar(self.saidas, e)
        return {"suportado": ok, "motivo": motivo, "microfones": microfones}

    @staticmethod
    def _validar_fonte(fonte: str) -> None:
        if fonte not in FONTES:
            raise ValueError("fonte de gravação desconhecida")

    def _abrir_captura(self, fonte: str, microfone: str | None):
        try:
            return self.dispositivos.abrir(fonte, microfone or None)
        except ErroCaptura:
            raise
        except Exception as e:  # noqa: BLE001
            erros.registrar(self.saidas, e)
            raise ErroCaptura(MSG_CAPTURA, "captura") from e

    def _publicar_nivel(self, origem: str, niveis: dict) -> None:
        self.publicar("nivel", {"origem": origem, "niveis": niveis})

    def iniciar_previa(self, fonte: str, microfone: str | None = None) -> None:
        """Liga (ou renova) o medidor de nível antes de gravar. Nada é gravado."""
        self._validar_fonte(fonte)
        with self._lock_previa:
            if self._ocupado():
                raise Ocupado(erros.MSG_OCUPADO)
            chave = (fonte, microfone or "")
            if self._previa and self._previa.viva and self._previa.chave == chave:
                self._previa.renovar()
                return
            self._parar_previa()
            self._previa = Previa(self._abrir_captura(fonte, microfone), self._publicar_nivel, chave)

    def _parar_previa(self) -> None:
        p, self._previa = self._previa, None
        if p:
            p.parar()

    def parar_previa(self) -> None:
        with self._lock_previa:
            self._parar_previa()

    def iniciar_gravacao(self, fonte: str, microfone: str | None = None) -> str:
        self._validar_fonte(fonte)
        with self.lock:
            if self.estado_modelo["fase"] != "pronto" or self.modelo is None:
                raise NaoPronto(erros.MSG_NAO_PRONTO)
            if self._ocupado():
                raise Ocupado(erros.MSG_OCUPADO)
        self.parar_previa()
        conjunto = self._abrir_captura(fonte, microfone)      # erros de microfone/som saem aqui, antes de criar a pasta
        nome = f"Gravação de {FONTES[fonte]} {time.strftime('%d-%m-%Y %Hh%M')}"
        try:
            id_, pasta, parar = self._comecar("gravacao", nome, {"fonte": fonte, "audio_guardado": True})
        except BaseException:
            conjunto.fechar()
            raise
        threading.Thread(target=self._executar, daemon=True, args=(
            id_, pasta, parar, lambda: self._fazer_gravacao(conjunto, pasta, parar), None, True)).start()
        return id_

    def _fazer_gravacao(self, conjunto, pasta: Path, parar: threading.Event) -> tuple[int, float | None]:
        def progresso(gravado_s: float, atraso_s: float, pendentes: int) -> None:
            self._atualizar_trabalho(fase="parando" if parar.is_set() else "gravando", gravado_s=round(gravado_s, 1),
                                     atraso_s=round(atraso_s), atrasado=atraso_s > ATRASO_AVISO_S,
                                     pendentes=pendentes, decorrido_s=round(gravado_s, 1))

        self._atualizar_trabalho(forcar=True, fase="parando" if parar.is_set() else "gravando")
        with open(pasta / ARQ_TEXTO, "a", encoding="utf-8") as f:
            def erro_trecho(t0: float, exc: BaseException) -> None:
                erros.registrar(self.saidas, exc)
                self._registrar_linha(f, Linha(t0, "[não consegui transcrever este trecho; o áudio ficou salvo]"))

            resultado = Gravacao(
                conjunto, str(pasta), self.modelo, parar,
                ao_linha=lambda linha: self._registrar_linha(f, linha), ao_erro=erro_trecho,
                ao_nivel=self._publicar_nivel, ao_progresso=progresso).executar()
        if resultado.falha:
            self._atualizar_trabalho(forcar=True, aviso_fim=resultado.falha.mensagem)
        self._atualizar_trabalho(forcar=True, gravado_s=round(resultado.gravado_s, 1), atraso_s=0, atrasado=False,
                                 pendentes=0)
        with self.lock:
            n = len(self.linhas)
        return n, resultado.gravado_s

    # ---------- atualizar o yt-dlp (só depois de o usuário confirmar) ----------
    def versao_ytdlp(self) -> str:
        return self._versao_ytdlp()

    def atualizar_ytdlp(self) -> dict:
        """Instala o yt-dlp mais recente. Recusa se houver trabalho em andamento. Levanta ErroYoutube."""
        with self.lock:
            if self._ocupado():
                raise Ocupado(erros.MSG_OCUPADO)
            self._atualizando = True
        try:
            return self._atualizar_ytdlp()
        except ErroYoutube as e:
            erros.registrar(self.saidas, e)
            raise
        finally:
            with self.lock:
                self._atualizando = False
            self._info_cache.clear()

    def encerrar(self) -> None:
        """Ao fechar o programa: desliga a prévia do microfone e pede para a gravação parar."""
        self.parar_previa()
        with self.lock:
            if self.trabalho and self.trabalho["fase"] in FASES_ATIVAS:
                self._parar.set()

    # ---------- texto ----------
    def texto(self, id_: str, horarios: bool = True) -> str | None:
        return self.historico.texto(id_, horarios)
