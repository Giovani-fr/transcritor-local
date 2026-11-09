"""Serviço da interface: modelo, transcrição de arquivo, parar e erros (modelo falso, áudio sintético)."""
import json
import threading
import time
import wave

import numpy as np
import pytest

from transcritor import erros
from transcritor.hardware import Hardware
from transcritor.historico import Historico
from transcritor.servico import NaoPronto, Ocupado, Servico, estimar_restante

HW = Hardware(False, "small", "cpu", "int8", aviso="aviso de teste")


class Seg:
    def __init__(self, a, b, t):
        self.start, self.end, self.text = a, b, t


class ModeloFalso:
    def __init__(self, segs=None, atraso=0.0, liberar: threading.Event | None = None):
        self.segs = segs if segs is not None else [Seg(0, 3, "Primeira frase."), Seg(3, 6, "Segunda frase."),
                                                   Seg(6, 9, "Obrigado."), Seg(9, 10, "Última frase.")]
        self.atraso, self.liberar = atraso, liberar

    def transcribe(self, audio, **kw):
        def gerar():
            for i, s in enumerate(self.segs):
                if self.liberar is not None and i == 1:
                    self.liberar.wait(5)
                time.sleep(self.atraso)
                yield s
        return gerar(), None


def esperar(cond, timeout=10.0):
    fim = time.time() + timeout
    while time.time() < fim:
        if cond():
            return True
        time.sleep(0.02)
    raise AssertionError("tempo esgotado esperando a condição")


def wav_sintetico(caminho, segundos=10):
    t = np.arange(segundos * 16000) / 16000
    x = (np.sin(2 * np.pi * 220 * t) * 0.3 * 32767).astype(np.int16)
    with wave.open(str(caminho), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes(x.tobytes())
    return str(caminho)


@pytest.fixture
def eventos():
    return []


@pytest.fixture
def servico(tmp_path, eventos):
    s = Servico(tmp_path / "saidas", HW, instalado=lambda n, c=None: True,
                carregar=lambda hw, c=None: (ModeloFalso(), hw),
                publicar=lambda tipo, dados: eventos.append((tipo, dados)))
    s.iniciar()
    esperar(lambda: s.estado_modelo["fase"] == "pronto")
    return s


def rodar(servico, caminho, nome="aula.wav", pasta_temp=None):
    id_ = servico.iniciar_arquivo(caminho, nome, "pt", pasta_temp)
    esperar(lambda: servico.trabalho["fase"] in ("concluido", "parado", "erro"))
    return id_


# ---------- estimativa ----------
def test_estimar_restante():
    assert estimar_restante(1.0, 0.5) is None           # cedo demais
    assert estimar_restante(10.0, 0.01) is None
    assert estimar_restante(30.0, 0.25) == pytest.approx(90.0)
    assert estimar_restante(60.0, 1.0) == 0.0


# ---------- modelo ----------
def test_primeira_abertura_sem_modelo_pede_download(tmp_path):
    s = Servico(tmp_path, HW, instalado=lambda n, c=None: False)
    s.iniciar()
    esperar(lambda: s.estado_modelo["fase"] == "faltando")
    assert s.hardware["aviso"] is True and s.hardware["modelo"] == "small"
    assert s.estado_modelo["tamanho_mb"] > 0


def test_download_com_progresso_e_depois_carrega(tmp_path, eventos):
    def baixar(nome, prog, cache):
        for f in (0.0, 0.5, 1.0):
            prog(f, f * 100, 100.0)

    s = Servico(tmp_path, HW, instalado=lambda n, c=None: False, baixar=baixar,
                carregar=lambda hw, c=None: (ModeloFalso(), hw),
                publicar=lambda t, d: eventos.append((t, d)))
    s.iniciar()
    esperar(lambda: s.estado_modelo["fase"] == "faltando")
    s.baixar_modelo()
    esperar(lambda: s.estado_modelo["fase"] == "pronto")
    fases = [d["modelo"]["fase"] for t, d in eventos if t == "estado"]
    assert "baixando" in fases and "carregando" in fases
    progressos = [d["modelo"]["progresso"] for t, d in eventos if t == "estado" and d["modelo"]["fase"] == "baixando"]
    assert 0.5 in progressos


def test_falha_no_download_volta_a_pedir_com_mensagem_simples(tmp_path):
    def baixar(*a):
        raise ConnectionError("Name or service not known")

    s = Servico(tmp_path, HW, instalado=lambda n, c=None: False, baixar=baixar)
    s.iniciar()
    esperar(lambda: s.estado_modelo["fase"] == "faltando")
    s.baixar_modelo()
    esperar(lambda: s.estado_modelo["erro"] != "")
    assert s.estado_modelo["fase"] == "faltando"
    assert s.estado_modelo["erro"] == erros.MSG_DOWNLOAD
    assert "Traceback" not in s.estado_modelo["erro"] and "service" not in s.estado_modelo["erro"]


def test_nao_aceita_arquivo_antes_do_modelo_ficar_pronto(tmp_path):
    s = Servico(tmp_path, HW, instalado=lambda n, c=None: False)
    with pytest.raises(NaoPronto):
        s.iniciar_arquivo("x.wav", "x.wav")


# ---------- transcrição de arquivo ----------
def test_transcreve_arquivo_com_progresso_e_salva(servico, eventos, tmp_path):
    pasta_temp = tmp_path / "tmp"; pasta_temp.mkdir()
    caminho = wav_sintetico(pasta_temp / "a.wav")
    id_ = rodar(servico, caminho, "Minha aula.wav", pasta_temp)

    t = servico.trabalho
    assert t["fase"] == "concluido" and t["progresso"] == 1.0 and t["duracao_s"] == 10.0
    textos = [d["texto"] for tipo, d in eventos if tipo == "linha"]
    assert textos == ["Primeira frase.", "Segunda frase.", "Última frase."]     # "Obrigado." filtrado
    progressos = [d["trabalho"]["progresso"] for tipo, d in eventos if tipo == "estado" and d["trabalho"]]
    assert progressos == sorted(progressos) and progressos[-1] == 1.0

    pasta = servico.historico.caminho(id_)
    assert (pasta / "transcricao.txt").read_text(encoding="utf-8").splitlines()[0] == "[00:00:00] Primeira frase."
    meta = json.loads((pasta / "meta.json").read_text(encoding="utf-8"))
    assert meta["status"] == "concluido" and meta["linhas"] == 3 and meta["nome"] == "Minha aula.wav"
    assert not pasta_temp.exists()                       # o arquivo enviado é apagado
    assert ("historico", {}) in eventos


def test_versoes_do_texto_com_e_sem_horarios(servico, tmp_path):
    id_ = rodar(servico, wav_sintetico(tmp_path / "a.wav"))
    com = servico.texto(id_, True)
    sem = servico.texto(id_, False)
    assert com.startswith("[00:00:00] Primeira frase.") and "[00:00:09] Última frase." in com
    assert sem == "Primeira frase. Segunda frase. Última frase."
    assert servico.texto("nao-existe") is None


def test_audio_sem_fala_avisa_em_vez_de_ficar_vazio(tmp_path):
    s = Servico(tmp_path / "s", HW, instalado=lambda n, c=None: True,
                carregar=lambda hw, c=None: (ModeloFalso(segs=[Seg(0, 3, "Obrigado.")]), hw))
    s.iniciar(); esperar(lambda: s.estado_modelo["fase"] == "pronto")
    rodar(s, wav_sintetico(tmp_path / "a.wav"))
    assert s.trabalho["fase"] == "concluido" and "Não encontrei fala" in s.trabalho["aviso"]


def test_parar_mantem_o_texto_parcial(tmp_path, eventos):
    liberar = threading.Event()
    s = Servico(tmp_path / "s", HW, instalado=lambda n, c=None: True,
                carregar=lambda hw, c=None: (ModeloFalso(liberar=liberar), hw),
                publicar=lambda t, d: eventos.append((t, d)))
    s.iniciar(); esperar(lambda: s.estado_modelo["fase"] == "pronto")
    id_ = s.iniciar_arquivo(wav_sintetico(tmp_path / "a.wav"), "a.wav")
    esperar(lambda: len(s.linhas) == 1)                 # a 1ª fala já saiu; a 2ª espera o `liberar`
    s.parar()
    assert s.trabalho["fase"] == "parando"
    liberar.set()
    esperar(lambda: s.trabalho["fase"] == "parado")
    meta = json.loads((s.historico.caminho(id_) / "meta.json").read_text(encoding="utf-8"))
    assert meta["status"] == "parado" and meta["linhas"] == 1
    assert s.texto(id_, True) == "[00:00:00] Primeira frase."
    assert "parou no meio" in s.trabalho["aviso"]


def test_parar_antes_de_sair_texto_explica(tmp_path):
    liberar = threading.Event()
    s = Servico(tmp_path / "s", HW, instalado=lambda n, c=None: True,
                carregar=lambda hw, c=None: (ModeloFalso(segs=[Seg(0, 3, "Só uma."), Seg(3, 6, "Outra.")],
                                                         atraso=0.5), hw))
    s.iniciar(); esperar(lambda: s.estado_modelo["fase"] == "pronto")
    s.iniciar_arquivo(wav_sintetico(tmp_path / "a.wav"), "a.wav")
    esperar(lambda: s.trabalho["fase"] == "transcrevendo")
    s.parar()
    esperar(lambda: s.trabalho["fase"] == "parado")
    assert s.trabalho["aviso"] == "Você parou antes de aparecer algum texto."


def test_arquivo_que_nao_e_audio_da_erro_em_linguagem_simples(servico, tmp_path):
    ruim = tmp_path / "falso.mp3"
    ruim.write_bytes(b"isto nao e um audio de verdade" * 50)
    rodar(servico, str(ruim), "falso.mp3")
    t = servico.trabalho
    assert t["fase"] == "erro" and t["erro"] == erros.MSG_ARQUIVO
    assert "Traceback" not in t["erro"] and "av." not in t["erro"]
    assert (servico.saidas / "registro.log").exists()    # o detalhe técnico fica no log, não na tela


def test_erro_inesperado_nao_derruba_a_thread(servico, tmp_path):
    servico.modelo = type("Quebrado", (), {"transcribe": lambda self, *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))})()
    rodar(servico, wav_sintetico(tmp_path / "a.wav"))
    assert servico.trabalho["fase"] == "erro" and servico.trabalho["erro"] == erros.MSG_GENERICA
    # e dá para transcrever de novo depois
    servico.modelo = ModeloFalso()
    rodar(servico, wav_sintetico(tmp_path / "b.wav"))
    assert servico.trabalho["fase"] == "concluido"


def test_recusa_segunda_transcricao_enquanto_ocupado(tmp_path):
    liberar = threading.Event()
    s = Servico(tmp_path / "s", HW, instalado=lambda n, c=None: True,
                carregar=lambda hw, c=None: (ModeloFalso(liberar=liberar), hw))
    s.iniciar(); esperar(lambda: s.estado_modelo["fase"] == "pronto")
    s.iniciar_arquivo(wav_sintetico(tmp_path / "a.wav"), "a.wav")
    esperar(lambda: len(s.linhas) == 1)
    with pytest.raises(Ocupado):
        s.iniciar_arquivo(wav_sintetico(tmp_path / "b.wav"), "b.wav")
    liberar.set()
    esperar(lambda: s.trabalho["fase"] == "concluido")


# ---------- histórico ----------
def test_historico_lista_e_bloqueia_caminhos_perigosos(servico, tmp_path):
    id_ = rodar(servico, wav_sintetico(tmp_path / "a.wav"), "Reunião de vendas (final).wav")
    itens = servico.historico.listar()
    assert [i["id"] for i in itens] == [id_] and itens[0]["status"] == "concluido"
    assert id_.endswith("Reunião-de-vendas-final")
    h = servico.historico
    for ruim in ("..", "../x", "a/b", "a\\b", "", "."):
        assert h.caminho(ruim) is None


def test_historico_marca_interrompido_e_ignora_pasta_vazia(tmp_path):
    h = Historico(tmp_path)
    id_, pasta = h.nova("x.wav")
    h.gravar_meta(pasta, {"nome": "x.wav", "status": "em_andamento"})
    assert h.listar() == []                                    # sem texto ainda: não aparece
    (pasta / "transcricao.txt").write_text("[00:00:01] oi\n", encoding="utf-8")
    assert h.listar()[0]["status"] == "interrompido"           # programa fechado no meio
