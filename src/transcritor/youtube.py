"""YouTube: informações do vídeo, legendas (modo Rápido) e download do áudio (modo Preciso).

Tudo que fala com a rede fica em ``ProvedorYoutube``, para os testes poderem trocá-lo por um falso.
Os erros viram ``ErroYoutube`` com mensagem em linguagem simples.
"""
from __future__ import annotations

import re
import sys
import threading
from pathlib import Path
from typing import Callable

from .texto import formatar_com_horarios, texto_corrido  # noqa: F401  (reexportados)

_ID = re.compile(r"(?:v=|youtu\.be/|/shorts/|/embed/|/live/)([\w-]{11})")

MSG_LINK = ("Esse não parece um link do YouTube. Cole o endereço completo do vídeo "
            "(começa com youtube.com ou youtu.be).")
MSG_PRIVADO = "Esse vídeo é privado: só quem publicou consegue assistir. Não dá para transcrevê-lo."
MSG_IDADE = ("Esse vídeo tem restrição de idade, e o YouTube só libera para quem está com a conta "
             "logada. Não consigo transcrevê-lo.")
MSG_INDISPONIVEL = ("Esse vídeo não está disponível. Ele pode ter sido removido ou estar bloqueado "
                    "no seu país.")
MSG_AO_VIVO = ("Esse vídeo é uma transmissão ao vivo que ainda não terminou. "
               "Espere a transmissão acabar para transcrever.")
MSG_REDE = "Não consegui falar com o YouTube. Confira sua conexão com a internet e tente de novo."
MSG_BLOQUEADO = ("O YouTube bloqueou o acesso por um tempo (isso acontece quando há muitos pedidos "
                 "seguidos). Espere alguns minutos e tente de novo.")
MSG_DOWNLOAD = ("Não consegui baixar o áudio desse vídeo. O YouTube muda com frequência, e o yt-dlp "
                "(o programa que faz o download) pode estar desatualizado.")
MSG_SEM_LEGENDA = ("Esse vídeo não tem legendas, então o modo Rápido não tem o que ler. "
                   "O modo Preciso baixa o áudio e transcreve aqui no seu computador: demora mais, "
                   "mas funciona mesmo sem legendas.")
MSG_GENERICA = "Não consegui ler as informações desse vídeo. Tente de novo em alguns instantes."


class ErroYoutube(Exception):
    """Erro já traduzido para o usuário. ``codigo`` deixa a interface oferecer a saída certa."""

    def __init__(self, mensagem: str, codigo: str, ajuda: dict | None = None):
        super().__init__(mensagem)
        self.mensagem, self.codigo, self.ajuda = mensagem, codigo, ajuda


class LinkInvalido(ErroYoutube, ValueError):
    """O texto colado não parece um link do YouTube."""

    def __init__(self, mensagem: str = MSG_LINK):
        super().__init__(mensagem, "link_invalido")


def extrair_id(url: str) -> str:
    url = (url or "").strip()
    m = _ID.search(url)
    if m:
        return m.group(1)
    if re.fullmatch(r"[\w-]{11}", url):
        return url
    raise LinkInvalido()


def url_canonica(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def ajuda_atualizar() -> dict:
    """Como atualizar o yt-dlp (isso quebra com frequência quando o YouTube muda)."""
    try:
        import yt_dlp
        versao = yt_dlp.version.__version__
    except Exception:
        versao = ""
    return {"tipo": "atualizar_ytdlp", "versao": versao,
            "comando": f'"{sys.executable}" -m pip install --upgrade yt-dlp'}


def classificar_ytdlp(mensagem: str) -> ErroYoutube:
    """Traduz o texto de erro do yt-dlp."""
    m = mensagem.lower()
    if "private video" in m:
        return ErroYoutube(MSG_PRIVADO, "privado")
    if "confirm your age" in m or "age-restricted" in m or "age restricted" in m or "inappropriate for some users" in m:
        return ErroYoutube(MSG_IDADE, "idade")
    if "not a bot" in m or "http error 429" in m or "too many requests" in m:
        return ErroYoutube(MSG_BLOQUEADO, "bloqueado", ajuda_atualizar())
    if any(x in m for x in ("video unavailable", "not available", "has been removed", "no longer available",
                            "been terminated", "blocked it", "does not exist", "copyright")):
        return ErroYoutube(MSG_INDISPONIVEL, "indisponivel")
    if any(x in m for x in ("getaddrinfo", "urlopen error", "timed out", "connection reset",
                            "connection refused", "network is unreachable", "name or service")):
        return ErroYoutube(MSG_REDE, "rede")
    return ErroYoutube(MSG_DOWNLOAD, "download", ajuda_atualizar())


def _texto(e: BaseException) -> str:
    """str(e) sem risco: algumas exceções das bibliotecas falham ao montar a própria mensagem."""
    try:
        return str(e)
    except Exception:
        return type(e).__name__


def traduzir_excecao(e: BaseException) -> ErroYoutube:
    """Qualquer exceção das bibliotecas do YouTube → ErroYoutube."""
    if isinstance(e, ErroYoutube):
        return e
    try:
        from youtube_transcript_api import _errors as t
    except Exception:  # pragma: no cover
        t = None
    if t is not None:
        if isinstance(e, t.InvalidVideoId):
            return LinkInvalido()
        if isinstance(e, (t.TranscriptsDisabled, t.NoTranscriptFound)):
            return ErroYoutube(MSG_SEM_LEGENDA, "sem_legenda")
        if isinstance(e, t.AgeRestricted):
            return ErroYoutube(MSG_IDADE, "idade")
        if isinstance(e, (t.IpBlocked, t.RequestBlocked)):
            return ErroYoutube(MSG_BLOQUEADO, "bloqueado")
        if isinstance(e, (t.VideoUnavailable, t.VideoUnplayable)):
            return (classificar_ytdlp(_texto(e)) if "private" in _texto(e).lower()
                    else ErroYoutube(MSG_INDISPONIVEL, "indisponivel"))
    try:
        import requests
        if isinstance(e, requests.exceptions.RequestException):
            return ErroYoutube(MSG_REDE, "rede")
    except Exception:  # pragma: no cover
        pass
    if isinstance(e, (ConnectionError, TimeoutError)):
        return ErroYoutube(MSG_REDE, "rede")
    return classificar_ytdlp(_texto(e)) if _texto(e) else ErroYoutube(MSG_GENERICA, "generico")


def escolher_legenda(opcoes: list[dict]) -> dict | None:
    """Prefere português manual > português automática > outro idioma manual > outro automática."""
    def chave(o):
        pt = o["idioma"].lower().startswith("pt")
        return (0 if pt else 1, 1 if o["automatica"] else 0)
    return min(opcoes, key=chave) if opcoes else None


class ProvedorYoutube:
    """Acesso real ao YouTube (yt-dlp + youtube-transcript-api)."""

    def info(self, url: str) -> dict:
        video_id = extrair_id(url)
        import yt_dlp
        opcoes = {"quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True,
                  "socket_timeout": 30}
        try:
            with yt_dlp.YoutubeDL(opcoes) as y:
                d = y.extract_info(url_canonica(video_id), download=False)
        except Exception as e:  # noqa: BLE001
            raise traduzir_excecao(e) from e
        if d.get("live_status") in ("is_live", "is_upcoming") or d.get("is_live"):
            raise ErroYoutube(MSG_AO_VIVO, "ao_vivo")
        return {"id": video_id, "url": url_canonica(video_id), "titulo": d.get("title") or video_id,
                "canal": d.get("channel") or d.get("uploader") or "", "duracao_s": d.get("duration") or 0}

    def listar_legendas(self, video_id: str) -> list[dict]:
        from youtube_transcript_api import YouTubeTranscriptApi
        try:
            lista = YouTubeTranscriptApi().list(video_id)
            return [{"idioma": t.language_code, "automatica": bool(t.is_generated)} for t in lista]
        except Exception as e:  # noqa: BLE001
            err = traduzir_excecao(e)
            if err.codigo == "sem_legenda":
                return []
            raise err from e

    def legendas(self, video_id: str) -> tuple[list[tuple[float, str]], str, bool]:
        """Devolve ([(segundos, texto)], idioma, automática?). Levanta ErroYoutube('sem_legenda')."""
        from youtube_transcript_api import YouTubeTranscriptApi
        try:
            lista = YouTubeTranscriptApi().list(video_id)
            por_idioma = {(t.language_code, bool(t.is_generated)): t for t in lista}
            escolhida = escolher_legenda([{"idioma": i, "automatica": a} for i, a in por_idioma])
            if escolhida is None:
                raise ErroYoutube(MSG_SEM_LEGENDA, "sem_legenda")
            dados = por_idioma[(escolhida["idioma"], escolhida["automatica"])].fetch()
        except ErroYoutube:
            raise
        except Exception as e:  # noqa: BLE001
            raise traduzir_excecao(e) from e
        segs = [(float(s.start), s.text.replace("\n", " ").strip()) for s in dados]
        return segs, escolhida["idioma"], escolhida["automatica"]

    def baixar_audio(self, url: str, pasta: Path, ao_progresso: Callable[[float, float | None], None],
                     parar: threading.Event | None = None) -> Path:
        """Baixa só o áudio (sem converter) para ``pasta`` e devolve o caminho do arquivo."""
        import yt_dlp
        from yt_dlp.utils import DownloadCancelled

        def gancho(d):
            if parar is not None and parar.is_set():
                raise DownloadCancelled("parado pelo usuário")
            if d.get("status") == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                if total:
                    ao_progresso(min(d.get("downloaded_bytes", 0) / total, 1.0), d.get("eta"))

        pasta = Path(pasta)
        opcoes = {"format": "bestaudio[ext=m4a]/bestaudio/best", "outtmpl": str(pasta / "audio.%(ext)s"),
                  "quiet": True, "no_warnings": True, "noprogress": True, "noplaylist": True,
                  "retries": 3, "socket_timeout": 30, "progress_hooks": [gancho]}
        try:
            with yt_dlp.YoutubeDL(opcoes) as y:
                y.download([url])
        except DownloadCancelled:
            raise ErroYoutube("Download interrompido.", "cancelado")
        except Exception as e:  # noqa: BLE001
            if parar is not None and parar.is_set():
                raise ErroYoutube("Download interrompido.", "cancelado") from e
            raise traduzir_excecao(e) from e
        arquivos = [p for p in pasta.glob("audio.*") if p.suffix not in (".part", ".ytdl")]
        if not arquivos:
            raise ErroYoutube(MSG_DOWNLOAD, "download", ajuda_atualizar())
        return arquivos[0]
