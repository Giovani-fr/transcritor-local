"""Fluxo YouTube do serviço (Rápido e Preciso) com provedor falso e áudio sintético. Sem rede."""
import json
import threading
import time

import pytest

from transcritor import youtube as yt
from transcritor.servico import Ocupado, Servico
from transcritor.youtube import ErroYoutube

from test_servico import HW, ModeloFalso, esperar, wav_sintetico

URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
INFO = {"id": "dQw4w9WgXcQ", "url": URL, "titulo": "Vídeo de teste", "canal": "Canal de teste", "duracao_s": 125}
LEGENDAS = [(0.0, "Primeira legenda."), (3.0, "Segunda legenda."), (6.0, ""), (8.0, "Terceira.")]


class YTFalso:
    def __init__(self, opcoes=None, legendas=None, erro_legendas=None, erro_download=None, lento=False):
        self.opcoes = opcoes if opcoes is not None else [{"idioma": "pt", "automatica": True}]
        self._legendas, self.erro_legendas = legendas or LEGENDAS, erro_legendas
        self.erro_download, self.lento = erro_download, lento
        self.pasta_baixada = None

    def info(self, url):
        if "invalido" in url:
            raise yt.LinkInvalido()
        return dict(INFO)

    def listar_legendas(self, video_id):
        if isinstance(self.opcoes, Exception):
            raise self.opcoes
        return self.opcoes

    def legendas(self, video_id):
        if self.erro_legendas:
            raise self.erro_legendas
        return self._legendas, "pt", True

    def baixar_audio(self, url, pasta, ao_progresso, parar=None):
        self.pasta_baixada = pasta
        for i in range(1, 5):
            if parar is not None and parar.is_set():
                raise ErroYoutube("Download interrompido.", "cancelado")
            ao_progresso(i / 4, 4 - i)
            time.sleep(0.15 if self.lento else 0)
        if self.erro_download:
            raise self.erro_download
        return wav_sintetico(pasta / "audio.wav") and (pasta / "audio.wav")


def criar(tmp_path, yt_falso, eventos=None, modelo=None):
    s = Servico(tmp_path / "saidas", HW, instalado=lambda n, c=None: True,
                carregar=lambda hw, c=None: (modelo or ModeloFalso(), hw), youtube=yt_falso,
                publicar=(lambda tipo, d: eventos.append((tipo, d))) if eventos is not None else None)
    s.iniciar()
    esperar(lambda: s.estado_modelo["fase"] == "pronto")
    return s


def terminar(s):
    esperar(lambda: s.trabalho["fase"] in ("concluido", "parado", "erro"))
    return s.trabalho


# ---------- consultar ----------
def test_consultar_mostra_titulo_duracao_e_legendas(tmp_path):
    s = criar(tmp_path, YTFalso())
    r = s.consultar_youtube(URL)
    assert (r["titulo"], r["duracao_s"], r["canal"]) == ("Vídeo de teste", 125, "Canal de teste")
    assert r["legendas"] == {"disponivel": True, "idioma": "pt", "automatica": True}


def test_consultar_video_sem_legenda(tmp_path):
    r = criar(tmp_path, YTFalso(opcoes=[])).consultar_youtube(URL)
    assert r["legendas"]["disponivel"] is False


def test_consultar_sem_saber_das_legendas_nao_trava(tmp_path):
    r = criar(tmp_path, YTFalso(opcoes=ErroYoutube("x", "bloqueado"))).consultar_youtube(URL)
    assert r["legendas"]["disponivel"] is None


def test_consultar_link_invalido(tmp_path):
    with pytest.raises(ErroYoutube) as e:
        criar(tmp_path, YTFalso()).consultar_youtube("link invalido")
    assert e.value.codigo == "link_invalido"


# ---------- modo Rápido ----------
def test_modo_rapido_usa_as_legendas(tmp_path):
    ev = []
    s = criar(tmp_path, YTFalso(), ev)
    id_ = s.iniciar_youtube(URL, "rapido")
    t = terminar(s)
    assert t["fase"] == "concluido" and t["tipo"] == "youtube" and t["nome"] == "Vídeo de teste"
    assert t["legenda"] == "pt (automática)"
    assert [d["texto"] for tipo, d in ev if tipo == "linha"] == ["Primeira legenda.", "Segunda legenda.", "Terceira."]
    assert s.texto(id_, True).splitlines()[1] == "[00:00:03] Segunda legenda."
    assert s.texto(id_, False) == "Primeira legenda. Segunda legenda. Terceira."
    meta = json.loads((s.historico.caminho(id_) / "meta.json").read_text(encoding="utf-8"))
    assert meta["tipo"] == "youtube" and meta["modo"] == "rapido" and meta["url"] == URL
    assert meta["canal"] == "Canal de teste" and meta["duracao_s"] == 125 and meta["status"] == "concluido"
    assert s.historico.listar()[0]["nome"] == "Vídeo de teste"


def test_modo_rapido_sem_legenda_oferece_o_preciso(tmp_path):
    s = criar(tmp_path, YTFalso(erro_legendas=ErroYoutube(yt.MSG_SEM_LEGENDA, "sem_legenda")))
    s.iniciar_youtube(URL, "rapido")
    t = terminar(s)
    assert t["fase"] == "erro" and t["codigo_erro"] == "sem_legenda" and "Preciso" in t["erro"]
    assert t["url"] == URL                                       # a tela precisa dela para trocar de modo
    assert s.historico.listar() == [] and not list((tmp_path / "saidas").glob("20*"))   # erro não fica no histórico


# ---------- modo Preciso ----------
def test_modo_preciso_baixa_transcreve_e_apaga_o_audio(tmp_path):
    ev, falso = [], YTFalso()
    s = criar(tmp_path, falso, ev)
    s.intervalo_pub = 0
    id_ = s.iniciar_youtube(URL, "preciso")
    t = terminar(s)
    assert t["fase"] == "concluido" and t["modo"] == "preciso" and not t["audio_guardado"]
    fases = [d["trabalho"]["fase"] for tipo, d in ev if tipo == "estado" and d["trabalho"]]
    assert fases.index("baixando") < fases.index("transcrevendo")                  # baixa e só depois transcreve
    prog_down = [d["trabalho"]["progresso"] for tipo, d in ev
                 if tipo == "estado" and d["trabalho"] and d["trabalho"]["fase"] == "baixando"]
    assert 0.5 in prog_down
    assert [d["texto"] for tipo, d in ev if tipo == "linha"] == ["Primeira frase.", "Segunda frase.", "Última frase."]
    assert not falso.pasta_baixada.exists()                                        # pasta temporária apagada
    assert not list(s.historico.caminho(id_).glob("audio.*"))                      # e nada guardado na saída


def test_modo_preciso_pode_guardar_o_audio(tmp_path):
    s = criar(tmp_path, YTFalso())
    id_ = s.iniciar_youtube(URL, "preciso", manter_audio=True)
    t = terminar(s)
    assert t["audio_guardado"] is True
    pasta = s.historico.caminho(id_)
    assert (pasta / "audio.wav").exists()
    assert json.loads((pasta / "meta.json").read_text(encoding="utf-8"))["audio_guardado"] is True


def test_parar_durante_o_download(tmp_path):
    s = criar(tmp_path, YTFalso(lento=True))
    s.iniciar_youtube(URL, "preciso")
    esperar(lambda: s.trabalho["fase"] == "baixando")
    s.parar()
    t = terminar(s)
    assert t["fase"] == "parado" and t["aviso"] == "Você parou antes de aparecer algum texto."
    assert s.historico.listar() == [] and not list((tmp_path / "saidas").glob("20*"))   # sem texto, nada fica guardado


def test_falha_no_download_sugere_atualizar_o_ytdlp(tmp_path):
    erro = yt.classificar_ytdlp("ERROR: unable to download video data: HTTP Error 403: Forbidden")
    s = criar(tmp_path, YTFalso(erro_download=erro))
    s.iniciar_youtube(URL, "preciso")
    t = terminar(s)
    assert t["fase"] == "erro" and t["codigo_erro"] == "download"
    assert t["ajuda"]["tipo"] == "atualizar_ytdlp" and "pip install --upgrade yt-dlp" in t["ajuda"]["comando"]
    assert "Traceback" not in t["erro"] and "403" not in t["erro"]
    assert s.historico.listar() == []


def test_erro_inesperado_no_download_nao_derruba_a_thread(tmp_path):
    s = criar(tmp_path, YTFalso(erro_download=RuntimeError("boom")))
    s.iniciar_youtube(URL, "preciso")
    assert terminar(s)["fase"] == "erro"
    s.yt = YTFalso()
    s.iniciar_youtube(URL, "rapido")
    assert terminar(s)["fase"] == "concluido"


def test_recusa_segunda_transcricao_do_youtube_enquanto_ocupado(tmp_path):
    s = criar(tmp_path, YTFalso(lento=True))
    s.iniciar_youtube(URL, "preciso")
    esperar(lambda: s.trabalho["fase"] == "baixando")
    with pytest.raises(Ocupado):
        s.iniciar_youtube(URL, "rapido")
    s.parar()
    terminar(s)


def test_link_invalido_ao_iniciar(tmp_path):
    s = criar(tmp_path, YTFalso())
    with pytest.raises(ErroYoutube):
        s.iniciar_youtube("link invalido", "rapido")
    assert s.trabalho is None


def test_ultima_atualizacao_de_progresso_nao_se_perde(tmp_path):
    """Várias atualizações dentro do intervalo: a última precisa chegar à tela mesmo sem novas atualizações."""
    ev = []
    s = criar(tmp_path, YTFalso(), ev)
    s.intervalo_pub = 0.2
    s.trabalho = {"fase": "baixando", "progresso": 0.0}
    for f in (0.1, 0.2, 0.45):                       # rajada rápida e depois silêncio (download parado)
        s._atualizar_trabalho(progresso=f)
    esperar(lambda: any(d["trabalho"] and d["trabalho"]["progresso"] == 0.45 for tipo, d in ev if tipo == "estado"))
