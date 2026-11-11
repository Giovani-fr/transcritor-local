"""Aplicação web (FastAPI): serve a página e expõe a API usada por ela."""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .. import config, diagnostico, erros
from ..captura import DispositivosSimulados, ErroCaptura
from ..hardware import detectar_hardware
from ..servico import FONTES, NaoPronto, Ocupado, Servico
from ..youtube import ErroYoutube

ESTATICOS = Path(__file__).parent / "static"
IDIOMAS = {"pt": "pt", "en": "en", "es": "es", "auto": None}


class PedidoLink(BaseModel):
    url: str


class PedidoYoutube(BaseModel):
    url: str
    modo: str = "rapido"
    manter_audio: bool = False
    idioma: str = "pt"


class PedidoGravacao(BaseModel):
    fonte: str = "reuniao"
    microfone: str = ""


class PedidoAtualizar(BaseModel):
    confirmado: bool = False


class Difusor:
    """Entrega os eventos do serviço (que vêm de threads) para as conexões SSE abertas."""

    def __init__(self):
        self.assinantes: set[tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = set()

    def publicar(self, tipo: str, dados: dict) -> None:
        for loop, fila in list(self.assinantes):
            try:
                loop.call_soon_threadsafe(fila.put_nowait, (tipo, dados))
            except RuntimeError:        # loop já fechado
                self.assinantes.discard((loop, fila))


def _sse(tipo: str, dados: dict) -> str:
    return f"event: {tipo}\ndata: {json.dumps(dados, ensure_ascii=False)}\n\n"


def servico_padrao() -> Servico:
    hw = lambda: detectar_hardware(config.forcar_cpu(), config.modelo_forcado(), config.cpu_threads())  # noqa: E731
    dispositivos = DispositivosSimulados() if config.captura_simulada() else None
    return Servico(config.pasta_saidas(), cache_dir=config.pasta_modelos(), detectar=hw, dispositivos=dispositivos)


def criar_app(servico: Servico | None = None, iniciar: bool = True) -> FastAPI:
    difusor = Difusor()
    servico = servico or servico_padrao()
    servico.publicar = difusor.publicar

    @asynccontextmanager
    async def vida(app):
        if iniciar:
            diagnostico.ativar(servico.saidas)        # um crash nativo deixa a pilha em saidas/crash.log
            servico.iniciar()
        yield
        servico.encerrar()
        if iniciar:
            diagnostico.desativar()

    app = FastAPI(title="transcritor-local", lifespan=vida)
    app.state.servico = servico

    @app.get("/", include_in_schema=False)
    def pagina():
        return FileResponse(ESTATICOS / "index.html", headers={"Cache-Control": "no-cache"})

    @app.get("/api/estado")
    def estado():
        return servico.snapshot()

    @app.get("/api/eventos")
    async def eventos():
        fila: asyncio.Queue = asyncio.Queue()
        par = (asyncio.get_running_loop(), fila)
        difusor.assinantes.add(par)

        async def gerar():
            try:
                yield _sse("snapshot", servico.snapshot())
                while True:
                    try:
                        tipo, dados = await asyncio.wait_for(fila.get(), 15)
                    except asyncio.TimeoutError:
                        yield ": ping\n\n"
                        continue
                    yield _sse(tipo, dados)
            finally:
                difusor.assinantes.discard(par)

        return StreamingResponse(gerar(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.post("/api/modelo/baixar")
    def baixar_modelo():
        servico.baixar_modelo()
        return {"ok": True}

    @app.post("/api/arquivo")
    def enviar_arquivo(arquivo: UploadFile = File(...), idioma: str = Form("pt")):
        if idioma not in IDIOMAS:
            raise HTTPException(400, "Idioma não reconhecido.")
        pasta_temp = Path(tempfile.mkdtemp(prefix="transcritor-"))
        destino = pasta_temp / ("arquivo" + Path(arquivo.filename or "").suffix.lower()[:10])
        try:
            with open(destino, "wb") as f:
                shutil.copyfileobj(arquivo.file, f, 1024 * 1024)
            id_ = servico.iniciar_arquivo(str(destino), arquivo.filename or "arquivo", IDIOMAS[idioma], pasta_temp)
        except (Ocupado, NaoPronto) as e:
            shutil.rmtree(pasta_temp, ignore_errors=True)
            raise HTTPException(409, str(e))
        except Exception as e:
            shutil.rmtree(pasta_temp, ignore_errors=True)
            erros.registrar(servico.saidas, e)
            raise HTTPException(500, erros.MSG_GENERICA)
        return {"id": id_}

    def _erro_youtube(e: ErroYoutube) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": e.mensagem, "codigo": e.codigo, "ajuda": e.ajuda})

    @app.post("/api/youtube/consultar")
    def consultar_youtube(pedido: PedidoLink):
        try:
            return servico.consultar_youtube(pedido.url)
        except ErroYoutube as e:
            return _erro_youtube(e)

    @app.post("/api/youtube")
    def iniciar_youtube(pedido: PedidoYoutube):
        if pedido.modo not in ("rapido", "preciso") or pedido.idioma not in IDIOMAS:
            raise HTTPException(400, "Opção não reconhecida.")
        try:
            return {"id": servico.iniciar_youtube(pedido.url, pedido.modo, pedido.manter_audio,
                                                  IDIOMAS[pedido.idioma])}
        except ErroYoutube as e:
            return _erro_youtube(e)
        except (Ocupado, NaoPronto) as e:
            raise HTTPException(409, str(e))
        except Exception as e:
            erros.registrar(servico.saidas, e)
            raise HTTPException(500, erros.MSG_GENERICA)

    # ---------- gravação ao vivo ----------
    def _erro_captura(e: ErroCaptura) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": e.mensagem, "codigo": e.codigo})

    @app.get("/api/gravacao/dispositivos")
    def dispositivos():
        return servico.info_gravacao()

    @app.post("/api/gravacao/previa")
    def ligar_previa(pedido: PedidoGravacao):
        if pedido.fonte not in FONTES:
            raise HTTPException(400, "Opção não reconhecida.")
        try:
            servico.iniciar_previa(pedido.fonte, pedido.microfone)
        except ErroCaptura as e:
            return _erro_captura(e)
        except Ocupado as e:
            raise HTTPException(409, str(e))
        return {"ok": True}

    @app.post("/api/gravacao/previa/parar")
    def desligar_previa():
        servico.parar_previa()
        return {"ok": True}

    @app.post("/api/gravacao")
    def iniciar_gravacao(pedido: PedidoGravacao):
        if pedido.fonte not in FONTES:
            raise HTTPException(400, "Opção não reconhecida.")
        try:
            return {"id": servico.iniciar_gravacao(pedido.fonte, pedido.microfone)}
        except ErroCaptura as e:
            return _erro_captura(e)
        except (Ocupado, NaoPronto) as e:
            raise HTTPException(409, str(e))
        except Exception as e:
            erros.registrar(servico.saidas, e)
            raise HTTPException(500, erros.MSG_GENERICA)

    # ---------- atualizar o yt-dlp (só com confirmação explícita) ----------
    @app.get("/api/ytdlp")
    def versao_ytdlp():
        return {"versao": servico.versao_ytdlp()}

    @app.post("/api/ytdlp/atualizar")
    def atualizar_ytdlp(pedido: PedidoAtualizar):
        if not pedido.confirmado:
            raise HTTPException(400, "Preciso da sua confirmação para atualizar o yt-dlp.")
        try:
            return servico.atualizar_ytdlp()
        except ErroYoutube as e:
            return _erro_youtube(e)
        except Ocupado as e:
            raise HTTPException(409, str(e))

    @app.post("/api/parar")
    def parar():
        servico.parar()
        return {"ok": True}

    @app.get("/api/historico")
    def historico():
        return servico.historico.listar()

    @app.get("/api/historico/{id_}/texto")
    def texto(id_: str, horarios: bool = True, baixar: bool = False):
        conteudo = servico.texto(id_, horarios)
        if conteudo is None:
            raise HTTPException(404, "Não encontrei essa transcrição.")
        cab = {}
        if baixar:
            nome = servico.historico.nome_amigavel(id_) + ("" if horarios else "-texto-corrido") + ".txt"
            cab["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(nome)}"
        return PlainTextResponse(conteudo, headers=cab)

    @app.post("/api/historico/{id_}/abrir-pasta")
    def abrir_pasta(id_: str):
        pasta = servico.historico.caminho(id_)
        if pasta is None:
            raise HTTPException(404, "Não encontrei essa transcrição.")
        if hasattr(os, "startfile"):
            os.startfile(str(pasta))  # noqa: S606 - abre o Explorer na pasta
        return {"ok": True, "pasta": str(pasta)}

    app.mount("/static", StaticFiles(directory=ESTATICOS), name="static")
    return app


app = criar_app()
