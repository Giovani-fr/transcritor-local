"""API web (FastAPI TestClient) com serviço falso, sem carregar o Whisper."""
import time

import pytest
from fastapi.testclient import TestClient

from transcritor import erros
from transcritor.servico import Servico
from transcritor.web.app import criar_app

from test_servico import HW, ModeloFalso, esperar, wav_sintetico


@pytest.fixture
def cliente(tmp_path):
    s = Servico(tmp_path / "saidas", HW, instalado=lambda n, c=None: True,
                carregar=lambda hw, c=None: (ModeloFalso(), hw))
    s.iniciar()
    esperar(lambda: s.estado_modelo["fase"] == "pronto")
    with TestClient(criar_app(s, iniciar=False)) as c:
        c.servico = s
        yield c


def enviar(cliente, caminho, nome="aula.wav", idioma="pt"):
    with open(caminho, "rb") as f:
        return cliente.post("/api/arquivo", files={"arquivo": (nome, f, "audio/wav")}, data={"idioma": idioma})


def test_pagina_e_estaticos(cliente):
    r = cliente.get("/")
    assert r.status_code == 200 and "Transcritor local" in r.text and "Avançado" in r.text
    assert cliente.get("/static/app.js").status_code == 200
    assert cliente.get("/static/style.css").status_code == 200


def test_estado_inclui_hardware_e_modelo(cliente):
    e = cliente.get("/api/estado").json()
    assert e["modelo"]["fase"] == "pronto" and e["hardware"]["aviso"] is True


def test_fluxo_completo_de_arquivo(cliente, tmp_path):
    r = enviar(cliente, wav_sintetico(tmp_path / "a.wav"), "Aula 1.wav")
    assert r.status_code == 200
    id_ = r.json()["id"]
    esperar(lambda: cliente.servico.trabalho["fase"] == "concluido")

    hist = cliente.get("/api/historico").json()
    assert hist[0]["id"] == id_ and hist[0]["nome"] == "Aula 1.wav"

    com = cliente.get(f"/api/historico/{id_}/texto").text
    assert com.startswith("[00:00:00] Primeira frase.")
    sem = cliente.get(f"/api/historico/{id_}/texto?horarios=false").text
    assert sem == "Primeira frase. Segunda frase. Última frase."

    baixar = cliente.get(f"/api/historico/{id_}/texto?baixar=true&horarios=false")
    disp = baixar.headers["content-disposition"]
    assert disp.startswith("attachment") and "Aula%201" in disp and disp.endswith(".txt")


def test_recusa_enquanto_ocupado_com_mensagem_simples(cliente, tmp_path):
    cliente.servico.modelo = ModeloFalso(atraso=0.3)
    assert enviar(cliente, wav_sintetico(tmp_path / "a.wav")).status_code == 200
    r = enviar(cliente, wav_sintetico(tmp_path / "b.wav"))
    assert r.status_code == 409 and r.json()["detail"] == erros.MSG_OCUPADO
    cliente.post("/api/parar")
    esperar(lambda: cliente.servico.trabalho["fase"] in ("parado", "concluido"))


def test_antes_do_modelo_pronto_responde_409(tmp_path):
    s = Servico(tmp_path / "s", HW, instalado=lambda n, c=None: False)
    with TestClient(criar_app(s, iniciar=False)) as c:
        r = enviar(c, wav_sintetico(tmp_path / "a.wav"))
        assert r.status_code == 409 and r.json()["detail"] == erros.MSG_NAO_PRONTO


def test_idioma_invalido_e_id_inexistente(cliente, tmp_path):
    assert enviar(cliente, wav_sintetico(tmp_path / "a.wav"), idioma="klingon").status_code == 400
    assert cliente.get("/api/historico/nao-existe/texto").status_code == 404
    assert cliente.post("/api/historico/nao-existe/abrir-pasta").status_code == 404


def test_parar_sem_trabalho_nao_quebra(cliente):
    assert cliente.post("/api/parar").json() == {"ok": True}


# ---------- YouTube ----------
def _cliente_yt(tmp_path, yt_falso):
    from test_servico_youtube import YTFalso  # noqa: F401
    sv = Servico(tmp_path / "saidas", HW, instalado=lambda n, c=None: True,
                 carregar=lambda hw, c=None: (ModeloFalso(), hw), youtube=yt_falso)
    sv.iniciar()
    esperar(lambda: sv.estado_modelo["fase"] == "pronto")
    return TestClient(criar_app(sv, iniciar=False)), sv


def test_api_youtube_consultar_e_transcrever(tmp_path):
    from test_servico_youtube import URL, YTFalso
    c, sv = _cliente_yt(tmp_path, YTFalso())
    with c:
        r = c.post("/api/youtube/consultar", json={"url": URL})
        assert r.status_code == 200 and r.json()["titulo"] == "Vídeo de teste"
        assert r.json()["legendas"]["disponivel"] is True
        r = c.post("/api/youtube", json={"url": URL, "modo": "rapido"})
        assert r.status_code == 200
        esperar(lambda: sv.trabalho["fase"] == "concluido")
        hist = c.get("/api/historico").json()
        assert hist[0]["nome"] == "Vídeo de teste" and hist[0]["tipo"] == "youtube"
        assert c.get(f"/api/historico/{r.json()['id']}/texto?horarios=false").text.startswith("Primeira legenda.")


def test_api_youtube_erros_em_linguagem_simples(tmp_path):
    from test_servico_youtube import URL, YTFalso
    c, sv = _cliente_yt(tmp_path, YTFalso())
    with c:
        r = c.post("/api/youtube/consultar", json={"url": "link invalido"})
        assert r.status_code == 400 and r.json()["codigo"] == "link_invalido"
        assert "youtube" in r.json()["detail"].lower() and "Traceback" not in r.json()["detail"]
        assert c.post("/api/youtube", json={"url": URL, "modo": "magico"}).status_code == 400
        assert c.post("/api/youtube", json={"url": "link invalido", "modo": "rapido"}).status_code == 400


def test_api_youtube_antes_do_modelo_pronto(tmp_path):
    from test_servico_youtube import URL, YTFalso
    sv = Servico(tmp_path / "s", HW, instalado=lambda n, c=None: False, youtube=YTFalso())
    with TestClient(criar_app(sv, iniciar=False)) as c:
        r = c.post("/api/youtube", json={"url": URL, "modo": "rapido"})
        assert r.status_code == 409 and r.json()["detail"] == erros.MSG_NAO_PRONTO


def test_api_usar_processador_recusa_durante_trabalho_e_aceita_parado(cliente):
    r = cliente.post("/api/hardware/processador", json={"usar": True})
    assert r.status_code == 200 and r.json() == {"ok": True}
    cliente.post("/api/hardware/processador", json={"usar": False})
