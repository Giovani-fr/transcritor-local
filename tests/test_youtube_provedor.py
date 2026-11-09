"""ProvedorYoutube e tradução de erros, sem rede (yt-dlp e youtube-transcript-api falsos)."""
import threading
from pathlib import Path

import pytest
import yt_dlp
import youtube_transcript_api
from youtube_transcript_api import _errors as t

from transcritor import youtube as yt
from transcritor.youtube import ErroYoutube, ProvedorYoutube, classificar_ytdlp, escolher_legenda

ID = "dQw4w9WgXcQ"


# ---------- tradução de erros ----------
@pytest.mark.parametrize("texto,codigo", [
    ("ERROR: [youtube] abc: Private video. Sign in if you've been granted access to this video", "privado"),
    ("ERROR: [youtube] abc: Sign in to confirm your age. This video may be inappropriate for some users.", "idade"),
    ("ERROR: [youtube] abc: Video unavailable", "indisponivel"),
    ("ERROR: [youtube] abc: Video unavailable. This video is no longer available because the YouTube "
     "account associated with this video has been terminated.", "indisponivel"),
    ("ERROR: [youtube] abc: Sign in to confirm you're not a bot. Use --cookies-from-browser", "bloqueado"),
    ("ERROR: Unable to download webpage: <urlopen error [Errno 11001] getaddrinfo failed>", "rede"),
    ("ERROR: unable to download video data: HTTP Error 403: Forbidden", "download"),
    ("ERROR: alguma coisa nunca vista", "download"),
])
def test_classifica_erros_do_ytdlp(texto, codigo):
    e = classificar_ytdlp(texto)
    assert e.codigo == codigo
    assert "ERROR" not in e.mensagem and "youtube]" not in e.mensagem     # nada técnico para o usuário


def test_falha_de_download_e_bloqueio_sugerem_atualizar_o_ytdlp():
    for texto in ("HTTP Error 403: Forbidden", "Sign in to confirm you're not a bot"):
        ajuda = classificar_ytdlp(texto).ajuda
        assert ajuda["tipo"] == "atualizar_ytdlp" and ajuda["comando"].endswith("-m pip install --upgrade yt-dlp")
    assert classificar_ytdlp("Private video").ajuda is None


def _exc(cls, msg=""):
    e = cls.__new__(cls)           # evita depender da assinatura do construtor de cada exceção
    Exception.__init__(e, msg)
    return e


@pytest.mark.parametrize("cls,codigo", [
    (t.TranscriptsDisabled, "sem_legenda"), (t.NoTranscriptFound, "sem_legenda"),
    (t.AgeRestricted, "idade"), (t.IpBlocked, "bloqueado"), (t.RequestBlocked, "bloqueado"),
    (t.VideoUnavailable, "indisponivel"), (t.InvalidVideoId, "link_invalido"),
])
def test_traduz_excecoes_das_legendas(cls, codigo):
    assert yt.traduzir_excecao(_exc(cls)).codigo == codigo


def test_traduz_rede():
    assert yt.traduzir_excecao(ConnectionError("x")).codigo == "rede"


def test_escolher_legenda_prefere_portugues_manual():
    ops = [{"idioma": "en", "automatica": False}, {"idioma": "pt", "automatica": True},
           {"idioma": "pt-BR", "automatica": False}]
    assert escolher_legenda(ops) == {"idioma": "pt-BR", "automatica": False}
    assert escolher_legenda([{"idioma": "en", "automatica": True}, {"idioma": "es", "automatica": False}]) == \
        {"idioma": "es", "automatica": False}
    assert escolher_legenda([]) is None


# ---------- yt-dlp falso ----------
class FakeYDL:
    info_ou_erro = None            # dict de retorno ou exceção
    download_erro = None
    eventos = []

    def __init__(self, opcoes):
        self.opcoes = opcoes

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def extract_info(self, url, download=False):
        FakeYDL.ultimo_url = url
        if isinstance(self.info_ou_erro, Exception):
            raise self.info_ou_erro
        return self.info_ou_erro

    def download(self, urls):
        if self.download_erro:
            raise self.download_erro
        pasta = Path(self.opcoes["outtmpl"]).parent
        (pasta / "audio.m4a.part").write_bytes(b"x")
        for d in self.eventos:
            for h in self.opcoes["progress_hooks"]:
                h(d)
        (pasta / "audio.m4a").write_bytes(b"audio")
        (pasta / "audio.m4a.part").unlink()


@pytest.fixture
def fake_ydl(monkeypatch):
    FakeYDL.info_ou_erro, FakeYDL.download_erro, FakeYDL.eventos = None, None, []
    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYDL)
    return FakeYDL


def test_info_devolve_titulo_e_duracao_e_usa_url_limpa(fake_ydl):
    fake_ydl.info_ou_erro = {"title": "Vídeo de teste", "channel": "Canal X", "duration": 125}
    r = ProvedorYoutube().info(f"https://www.youtube.com/watch?v={ID}&list=PLxyz&t=5s")
    assert r == {"id": ID, "url": f"https://www.youtube.com/watch?v={ID}", "titulo": "Vídeo de teste",
                 "canal": "Canal X", "duracao_s": 125}
    assert fake_ydl.ultimo_url == f"https://www.youtube.com/watch?v={ID}"       # sem a playlist


def test_info_link_invalido_nem_chama_a_rede(fake_ydl):
    with pytest.raises(ErroYoutube) as e:
        ProvedorYoutube().info("https://exemplo.com/video")
    assert e.value.codigo == "link_invalido"


def test_info_traduz_video_privado(fake_ydl):
    fake_ydl.info_ou_erro = yt_dlp.utils.DownloadError("ERROR: [youtube] x: Private video. Sign in if...")
    with pytest.raises(ErroYoutube) as e:
        ProvedorYoutube().info(ID)
    assert e.value.codigo == "privado"


def test_info_recusa_transmissao_ao_vivo(fake_ydl):
    fake_ydl.info_ou_erro = {"title": "Ao vivo", "live_status": "is_live"}
    with pytest.raises(ErroYoutube) as e:
        ProvedorYoutube().info(ID)
    assert e.value.codigo == "ao_vivo"


def test_baixar_audio_reporta_progresso_e_devolve_o_arquivo(fake_ydl, tmp_path):
    fake_ydl.eventos = [{"status": "downloading", "downloaded_bytes": 25, "total_bytes": 100, "eta": 9},
                        {"status": "downloading", "downloaded_bytes": 100, "total_bytes_estimate": 100, "eta": 0},
                        {"status": "finished"}]
    pontos = []
    arq = ProvedorYoutube().baixar_audio(yt.url_canonica(ID), tmp_path, lambda f, e: pontos.append((f, e)))
    assert arq.name == "audio.m4a" and arq.read_bytes() == b"audio"      # ignora o .part
    assert pontos == [(0.25, 9), (1.0, 0)]


def test_baixar_audio_cancelado_pelo_usuario(fake_ydl, tmp_path):
    parar = threading.Event()
    parar.set()
    fake_ydl.eventos = [{"status": "downloading", "downloaded_bytes": 1, "total_bytes": 10}]
    with pytest.raises(ErroYoutube) as e:
        ProvedorYoutube().baixar_audio(yt.url_canonica(ID), tmp_path, lambda f, e: None, parar)
    assert e.value.codigo == "cancelado"


def test_baixar_audio_falha_sugere_atualizar(fake_ydl, tmp_path):
    fake_ydl.download_erro = yt_dlp.utils.DownloadError("ERROR: unable to download video data: HTTP Error 403: Forbidden")
    with pytest.raises(ErroYoutube) as e:
        ProvedorYoutube().baixar_audio(yt.url_canonica(ID), tmp_path, lambda f, e: None)
    assert e.value.codigo == "download" and e.value.ajuda["tipo"] == "atualizar_ytdlp"


# ---------- legendas falsas ----------
class Trecho:
    def __init__(self, start, text):
        self.start, self.text = start, text


class Transcricao:
    def __init__(self, idioma, auto, textos):
        self.language_code, self.is_generated, self._textos = idioma, auto, textos

    def fetch(self):
        return [Trecho(i * 2.0, x) for i, x in enumerate(self._textos)]


@pytest.fixture
def fake_api(monkeypatch):
    class Api:
        lista = []
        erro = None

        def list(self, video_id):
            if Api.erro:
                raise Api.erro
            return Api.lista
    monkeypatch.setattr(youtube_transcript_api, "YouTubeTranscriptApi", Api)
    return Api


def test_legendas_escolhe_portugues_e_junta_quebras_de_linha(fake_api):
    fake_api.lista = [Transcricao("en", False, ["Hello"]), Transcricao("pt", True, ["Olá\ntudo bem", "Segunda"])]
    segs, idioma, auto = ProvedorYoutube().legendas(ID)
    assert (idioma, auto) == ("pt", True)
    assert segs == [(0.0, "Olá tudo bem"), (2.0, "Segunda")]
    assert ProvedorYoutube().listar_legendas(ID) == [{"idioma": "en", "automatica": False},
                                                     {"idioma": "pt", "automatica": True}]


def test_sem_legendas_vira_erro_com_saida_para_o_modo_preciso(fake_api):
    fake_api.lista = []
    with pytest.raises(ErroYoutube) as e:
        ProvedorYoutube().legendas(ID)
    assert e.value.codigo == "sem_legenda" and "Preciso" in e.value.mensagem


def test_legendas_desativadas_listar_devolve_vazio(fake_api):
    fake_api.erro = _exc(t.TranscriptsDisabled)
    assert ProvedorYoutube().listar_legendas(ID) == []
    with pytest.raises(ErroYoutube) as e:
        ProvedorYoutube().legendas(ID)
    assert e.value.codigo == "sem_legenda"


def test_legendas_bloqueio_do_youtube(fake_api):
    fake_api.erro = _exc(t.IpBlocked)
    with pytest.raises(ErroYoutube) as e:
        ProvedorYoutube().legendas(ID)
    assert e.value.codigo == "bloqueado"
