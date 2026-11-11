"""Captura em processo separado: o servidor sobrevive à morte do processo e o áudio já salvo permanece."""
import threading
import time
import wave

import pytest
from fastapi.testclient import TestClient

from transcritor.captura import MSG_PROCESSO, DispositivosSimulados, ErroCaptura
from transcritor.captura_proc import DispositivosIsolados
from transcritor.servico import Servico
from transcritor.web.app import criar_app

from fabricas_captura import DispositivosQueCaem, DispositivosQueTravam
from test_servico import HW, ModeloFalso, esperar

SR = 16000
FINAIS = ("concluido", "parado", "erro")


@pytest.fixture
def isolados(tmp_path):
    d = DispositivosIsolados(DispositivosSimulados, saidas=tmp_path / "saidas")
    yield d
    d.encerrar()


def criar_servico(tmp_path, fabrica, eventos=None):
    disp = DispositivosIsolados(fabrica, saidas=tmp_path / "saidas")
    s = Servico(tmp_path / "saidas", HW, instalado=lambda n, c=None: True,
                carregar=lambda hw, c=None: (ModeloFalso(), hw), dispositivos=disp,
                publicar=(lambda t, d: eventos.append((t, d))) if eventos is not None else None)
    s.iniciar()
    esperar(lambda: s.estado_modelo["fase"] == "pronto")
    return s, disp


def test_audio_chega_do_processo_filho(isolados):
    c = isolados.abrir("reuniao", None)
    assert [x.rotulo for x in c.capturas] == ["mic", "sistema"]
    time.sleep(1.5)
    assert all(x.ativa() for x in c.capturas)
    assert sum(len(x.coletar()) for x in c.capturas) > SR     # chegou áudio sintético
    c.fechar()
    assert isolados.processo.inicios == 1


def test_lista_de_microfones_fica_em_cache(isolados, monkeypatch):
    chamadas = []
    original = isolados.processo.chamar
    monkeypatch.setattr(isolados.processo, "chamar", lambda *a, **k: (chamadas.append(a[0]), original(*a, **k))[1])
    primeira = isolados.microfones()
    for _ in range(5):
        assert isolados.microfones() == primeira
    assert chamadas == ["microfones"]


def test_chamadas_simultaneas_nao_derrubam_nem_reiniciam_o_filho(isolados):
    """O mesmo cenário do travamento real (listar + abrir/fechar ao mesmo tempo), agora em outro processo."""
    fim, erros = time.time() + 3, []

    def listar():
        while time.time() < fim:
            try:
                isolados.microfones()
            except Exception as e:  # noqa: BLE001
                erros.append(e)

    def abrir_e_fechar(fonte):
        while time.time() < fim:
            try:
                isolados.abrir(fonte, None).fechar()
            except Exception as e:  # noqa: BLE001
                erros.append(e)

    ts = [threading.Thread(target=listar) for _ in range(3)]
    ts += [threading.Thread(target=abrir_e_fechar, args=(f,)) for f in ("ambiente", "reuniao", "ambiente")]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert not erros and isolados.processo.inicios == 1 and isolados.processo.viva


def test_processo_morto_no_meio_da_gravacao_servidor_segue_no_ar(tmp_path):
    eventos = []
    s, disp = criar_servico(tmp_path, DispositivosSimulados, eventos)
    cliente = TestClient(criar_app(s, iniciar=False))
    s.publicar = lambda t, d: eventos.append((t, d))               # criar_app troca o publicar; volta a escutar
    try:
        id_ = s.iniciar_gravacao("ambiente", "")
        esperar(lambda: s.trabalho["fase"] == "gravando" and s.trabalho["gravado_s"] >= 2, 15)
        disp.processo._proc.kill()                                  # o processo de captura morre de repente
        esperar(lambda: s.trabalho["fase"] in FINAIS, 15)

        t = s.trabalho
        assert t["aviso"] == MSG_PROCESSO == "A captura de áudio parou. O que já foi gravado ficou salvo."
        assert ("aviso", {"texto": MSG_PROCESSO}) in eventos
        wav = s.historico.caminho(id_) / "audio_001.wav"
        with wave.open(str(wav)) as w:                              # o áudio salvo até ali continua legível
            assert w.getnframes() / SR > 1.5
        assert cliente.get("/api/estado").status_code == 200       # o servidor não caiu
        assert "terminou de repente" in (tmp_path / "saidas" / "registro.log").read_text(encoding="utf-8")

        id2 = s.iniciar_gravacao("ambiente", "")                    # e dá para gravar de novo
        esperar(lambda: s.trabalho["fase"] == "gravando" and s.trabalho["gravado_s"] >= 1, 15)
        s.parar()
        esperar(lambda: s.trabalho["fase"] in FINAIS, 15)
        assert s.trabalho["aviso"] == "" and id2 != id_ and disp.processo.inicios == 2
        assert (s.historico.caminho(id_) / "audio_001.wav").exists()      # o áudio anterior permanece
    finally:
        s.encerrar()


def test_acesso_invalido_de_verdade_no_filho_nao_derruba_o_servidor(tmp_path):
    s, disp = criar_servico(tmp_path, DispositivosQueCaem)
    cliente = TestClient(criar_app(s, iniciar=False))
    try:
        id_ = s.iniciar_gravacao("ambiente", "")
        esperar(lambda: s.trabalho["fase"] in FINAIS, 20)
        assert s.trabalho["aviso"] == MSG_PROCESSO
        assert cliente.get("/api/estado").status_code == 200
        assert (s.historico.caminho(id_) / "audio_001.wav").exists()
        log = (tmp_path / "saidas" / "crash-captura.log").read_text(encoding="utf-8")
        assert "access violation" in log                           # a pilha nativa ficou registrada
    finally:
        s.encerrar()


def test_previa_acaba_quando_o_filho_morre_e_a_proxima_renovacao_religa(tmp_path):
    s, disp = criar_servico(tmp_path, DispositivosSimulados)
    try:
        s.iniciar_previa("ambiente", "")
        esperar(lambda: s._previa and s._previa.viva)
        disp.processo._proc.kill()
        esperar(lambda: not s._previa.viva, 10)
        s.iniciar_previa("ambiente", "")                            # a página renova a cada 5 s
        assert s._previa.viva and disp.processo.inicios == 2
    finally:
        s.encerrar()


def test_filho_travado_vira_erro_simples_e_e_encerrado(tmp_path):
    d = DispositivosIsolados(DispositivosQueTravam, saidas=tmp_path, timeout_s=1.5)
    try:
        with pytest.raises(ErroCaptura) as e:
            d.microfones()
        assert e.value.mensagem == MSG_PROCESSO
        esperar(lambda: not d.processo.viva, 5)
    finally:
        d.encerrar()
