"""API da aba Gravar e do botão de atualizar o yt-dlp (dispositivos e pip falsos)."""
import pytest
from fastapi.testclient import TestClient

from transcritor import erros
from transcritor.captura import MSG_SEM_MIC
from transcritor.servico import Servico
from transcritor.web.app import criar_app
from transcritor.youtube import ErroYoutube

from test_servico import HW, ModeloFalso, esperar
from test_servico_gravacao import Dispositivos


def fazer_cliente(tmp_path, disp=None, atualizar=None):
    s = Servico(tmp_path / "saidas", HW, instalado=lambda n, c=None: True,
                carregar=lambda hw, c=None: (ModeloFalso(), hw), dispositivos=disp or Dispositivos(),
                atualizar_ytdlp=atualizar or (lambda: {"antes": "1", "depois": "2", "mudou": True, "reiniciar": False}),
                versao_ytdlp=lambda: "2025.01.01")
    s.iniciar()
    esperar(lambda: s.estado_modelo["fase"] == "pronto")
    return s


@pytest.fixture
def cliente(tmp_path):
    s = fazer_cliente(tmp_path)
    with TestClient(criar_app(s, iniciar=False)) as c:
        c.servico = s
        yield c


def test_dispositivos(cliente):
    r = cliente.get("/api/gravacao/dispositivos").json()
    assert r["suportado"] is True and r["microfones"][0]["nome"] == "Mic A"


def test_fluxo_de_gravacao_pela_api(cliente):
    r = cliente.post("/api/gravacao", json={"fonte": "ambiente", "microfone": "Mic A"})
    assert r.status_code == 200
    id_ = r.json()["id"]
    esperar(lambda: cliente.servico.trabalho["fase"] == "gravando" and cliente.servico.trabalho["gravado_s"] >= 3)
    assert cliente.get("/api/estado").json()["trabalho"]["tipo"] == "gravacao"
    assert cliente.post("/api/gravacao", json={"fonte": "ambiente"}).status_code == 409
    cliente.post("/api/parar")
    esperar(lambda: cliente.servico.trabalho["fase"] == "concluido")
    hist = cliente.get("/api/historico").json()
    assert hist[0]["id"] == id_ and hist[0]["tipo"] == "gravacao" and hist[0]["fonte"] == "ambiente"
    assert cliente.get(f"/api/historico/{id_}/texto").text.startswith("[00:00:00] Primeira frase.")


def test_fonte_invalida(cliente):
    assert cliente.post("/api/gravacao", json={"fonte": "lua"}).status_code == 400
    assert cliente.post("/api/gravacao/previa", json={"fonte": "lua"}).status_code == 400


def test_sem_microfone_devolve_mensagem_simples(tmp_path):
    s = fazer_cliente(tmp_path, Dispositivos(sem_mic=True))
    with TestClient(criar_app(s, iniciar=False)) as c:
        r = c.post("/api/gravacao", json={"fonte": "ambiente"})
        assert r.status_code == 400 and r.json() == {"detail": MSG_SEM_MIC, "codigo": "sem_microfone"}
        r = c.post("/api/gravacao/previa", json={"fonte": "reuniao"})
        assert r.status_code == 400 and r.json()["codigo"] == "sem_microfone"


def test_previa_liga_renova_e_desliga(cliente):
    disp = cliente.servico.dispositivos
    assert cliente.post("/api/gravacao/previa", json={"fonte": "ambiente", "microfone": "Mic A"}).status_code == 200
    assert cliente.post("/api/gravacao/previa", json={"fonte": "ambiente", "microfone": "Mic A"}).status_code == 200
    assert len(disp.abertos) == 1
    assert cliente.post("/api/gravacao/previa/parar").status_code == 200
    assert disp.abertos[0].capturas[0].fechada


def test_fechar_o_programa_desliga_o_microfone(tmp_path):
    s = fazer_cliente(tmp_path)
    with TestClient(criar_app(s, iniciar=False)) as c:
        c.post("/api/gravacao/previa", json={"fonte": "ambiente"})
        disp = s.dispositivos
    assert disp.abertos[0].capturas[0].fechada


# ---------- atualizar o yt-dlp ----------
def test_versao_do_ytdlp(cliente):
    assert cliente.get("/api/ytdlp").json() == {"versao": "2025.01.01"}


def test_atualizar_exige_confirmacao_explicita(tmp_path):
    chamadas = []
    s = fazer_cliente(tmp_path, atualizar=lambda: chamadas.append(1) or {"antes": "1", "depois": "2",
                                                                        "mudou": True, "reiniciar": False})
    with TestClient(criar_app(s, iniciar=False)) as c:
        assert c.post("/api/ytdlp/atualizar", json={}).status_code == 400
        assert c.post("/api/ytdlp/atualizar", json={"confirmado": False}).status_code == 400
        assert c.post("/api/ytdlp/atualizar").status_code == 422
        assert chamadas == []                                     # nada foi instalado sem confirmação
        r = c.post("/api/ytdlp/atualizar", json={"confirmado": True})
        assert r.status_code == 200 and r.json() == {"antes": "1", "depois": "2", "mudou": True, "reiniciar": False}
        assert chamadas == [1]


def test_atualizar_erro_em_linguagem_simples(tmp_path):
    def falha():
        raise ErroYoutube("Não consegui baixar a atualização.", "atualizacao")

    s = fazer_cliente(tmp_path, atualizar=falha)
    with TestClient(criar_app(s, iniciar=False)) as c:
        r = c.post("/api/ytdlp/atualizar", json={"confirmado": True})
        assert r.status_code == 400 and r.json()["detail"] == "Não consegui baixar a atualização."


def test_atualizar_recusa_com_trabalho_em_andamento(cliente):
    cliente.post("/api/gravacao", json={"fonte": "ambiente"})
    esperar(lambda: cliente.servico.trabalho["fase"] == "gravando")
    r = cliente.post("/api/ytdlp/atualizar", json={"confirmado": True})
    assert r.status_code == 409 and r.json()["detail"] == erros.MSG_OCUPADO
    cliente.post("/api/parar")
    esperar(lambda: cliente.servico.trabalho["fase"] == "concluido")
