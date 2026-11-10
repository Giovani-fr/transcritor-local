"""Atualizar o yt-dlp: comando, versões antes/depois e erros em linguagem simples (pip falso)."""
import subprocess
import sys

import pytest

from transcritor import youtube
from transcritor.youtube import ErroYoutube, atualizar_ytdlp


class Pip:
    def __init__(self, codigo=0, saida="", erro="", levanta=None):
        self.codigo, self.saida, self.erro, self.levanta, self.comandos = codigo, saida, erro, levanta, []

    def __call__(self, comando):
        self.comandos.append(comando)
        if self.levanta:
            raise self.levanta
        return subprocess.CompletedProcess(comando, self.codigo, self.saida, self.erro)


def versoes(*seq):
    it = iter(seq)
    return lambda: next(it)


def test_atualiza_e_mostra_antes_e_depois():
    pip = Pip()
    r = atualizar_ytdlp(pip, versoes("2024.01.01", "2025.06.30"), lambda: "2025.06.30")
    assert r == {"antes": "2024.01.01", "depois": "2025.06.30", "mudou": True, "reiniciar": False}
    assert pip.comandos == [[sys.executable, "-m", "pip", "install", "--upgrade", "yt-dlp"]]


def test_ja_estava_na_ultima_versao():
    r = atualizar_ytdlp(Pip(), versoes("2025.06.30", "2025.06.30"), lambda: "2025.06.30")
    assert r["mudou"] is False and r["antes"] == r["depois"] and r["reiniciar"] is False


def test_pede_reinicio_quando_nao_da_para_recarregar():
    r = atualizar_ytdlp(Pip(), versoes("1", "2"), lambda: "")
    assert r["reiniciar"] is True and r["depois"] == "2"
    r = atualizar_ytdlp(Pip(), versoes("1", "2"), lambda: "1")          # recarregou, mas ainda é a antiga
    assert r["reiniciar"] is True


def test_sem_internet_mensagem_simples():
    pip = Pip(codigo=1, erro="WARNING: Retrying ... Failed to establish a new connection: getaddrinfo failed")
    with pytest.raises(ErroYoutube) as e:
        atualizar_ytdlp(pip, versoes("1"), lambda: "1")
    assert e.value.mensagem == youtube.MSG_ATUALIZAR_REDE and e.value.codigo == "atualizacao"
    assert "pip" not in e.value.mensagem and "getaddrinfo" not in e.value.mensagem


def test_falha_qualquer_oferece_o_jeito_manual():
    with pytest.raises(ErroYoutube) as e:
        atualizar_ytdlp(Pip(codigo=1, erro="ERROR: Permission denied: 'site-packages'"), versoes("1"), lambda: "1")
    assert e.value.mensagem == youtube.MSG_ATUALIZAR_FALHA and "Permission" not in e.value.mensagem


def test_tempo_esgotado_e_erro_inesperado():
    with pytest.raises(ErroYoutube) as e:
        atualizar_ytdlp(Pip(levanta=subprocess.TimeoutExpired("pip", 300)), versoes("1"), lambda: "1")
    assert e.value.mensagem == youtube.MSG_ATUALIZAR_REDE
    with pytest.raises(ErroYoutube) as e:
        atualizar_ytdlp(Pip(levanta=FileNotFoundError("sem python")), versoes("1"), lambda: "1")
    assert e.value.mensagem == youtube.MSG_ATUALIZAR_FALHA


def test_versao_instalada_e_recarga_funcionam_de_verdade():
    """Sem rede e sem instalar nada: só lê a versão e confirma que a recarga traz o yt-dlp de volta."""
    v = youtube.versao_ytdlp()
    assert v and v[0].isdigit()
    carregada = youtube._recarregar_ytdlp()
    assert carregada == v and "yt_dlp" in sys.modules
