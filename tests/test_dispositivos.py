"""Dispositivos de captura com módulo pyaudiowpatch falso: nenhum microfone real é aberto."""
import sys
import types

import numpy as np
import pytest

from transcritor.captura import (MSG_MIC_ABRIR, MSG_NAO_WINDOWS, MSG_SEM_COMPONENTE, MSG_SEM_MIC, MSG_SEM_SISTEMA,
                                 MSG_SISTEMA_ABRIR, DispositivosSimulados, DispositivosWasapi, ErroCaptura,
                                 FonteSimulada, nivel_visual)


class FluxoFalso:
    def __init__(self):
        self.fechado = False

    def stop_stream(self):
        pass

    def close(self):
        self.fechado = True

    def is_active(self):
        return not self.fechado


def instalar_pyaudio_falso(monkeypatch, dispositivos, padrao_entrada=-1, loopback=None, falha_abrir=()):
    fluxos, estado = [], {"terminou": 0}

    class PA:
        def get_host_api_info_by_type(self, tipo):
            return {"index": 0, "deviceCount": len(dispositivos), "defaultInputDevice": padrao_entrada}

        def get_device_info_by_host_api_device_index(self, host, i):
            return dispositivos[i]

        def get_default_wasapi_loopback(self):
            if loopback is None:
                raise LookupError("sem loopback")
            return loopback

        def open(self, **kw):
            if kw["input_device_index"] in falha_abrir:
                raise OSError("dispositivo em uso")
            f = FluxoFalso()
            fluxos.append(f)
            return f

        def terminate(self):
            estado["terminou"] += 1

    modulo = types.SimpleNamespace(PyAudio=PA, paWASAPI=13, paInt16=8, paContinue=0)
    monkeypatch.setitem(sys.modules, "pyaudiowpatch", modulo)
    monkeypatch.setattr(sys, "platform", "win32")
    return fluxos, estado


def dev(i, nome, canais=1, loop=False, taxa=48000):
    return {"index": i, "name": nome, "maxInputChannels": canais, "defaultSampleRate": taxa,
            "isLoopbackDevice": loop}


def test_nao_suportado_fora_do_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    d = DispositivosWasapi()
    assert d.suportado() == (False, MSG_NAO_WINDOWS)
    with pytest.raises(ErroCaptura) as e:
        d.abrir("ambiente")
    assert e.value.codigo == "nao_suportado"


def test_sem_o_componente_de_captura(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "pyaudiowpatch", None)       # o import falha
    assert DispositivosWasapi().suportado() == (False, MSG_SEM_COMPONENTE)


def test_lista_microfones_sem_loopback_e_sem_repetidos(monkeypatch):
    instalar_pyaudio_falso(monkeypatch, [dev(0, "Microfone A"), dev(1, "Alto-falante [Loopback]", 2, True),
                                         dev(2, "Fone"), dev(3, "Fone"), dev(4, "Saída", 0)], padrao_entrada=2)
    assert DispositivosWasapi().microfones() == [
        {"id": "Microfone A", "nome": "Microfone A", "padrao": False},
        {"id": "Fone", "nome": "Fone", "padrao": True}]


def test_ambiente_abre_so_o_microfone_escolhido(monkeypatch):
    fluxos, estado = instalar_pyaudio_falso(monkeypatch, [dev(0, "Mic A"), dev(1, "Mic B")], padrao_entrada=0)
    c = DispositivosWasapi().abrir("ambiente", "Mic B")
    assert [x.rotulo for x in c.capturas] == ["mic"] and len(fluxos) == 1
    c.fechar()
    c.fechar()                                                   # fechar duas vezes é inofensivo
    assert fluxos[0].fechado and estado["terminou"] == 1


def test_microfone_escolhido_que_sumiu_cai_no_padrao(monkeypatch):
    instalar_pyaudio_falso(monkeypatch, [dev(0, "Mic A"), dev(1, "Mic B")], padrao_entrada=1)
    c = DispositivosWasapi().abrir("ambiente", "Fone que foi desconectado")
    assert len(c.capturas) == 1
    c.fechar()


def test_reuniao_abre_microfone_e_loopback(monkeypatch):
    fluxos, _ = instalar_pyaudio_falso(monkeypatch, [dev(0, "Mic A")], padrao_entrada=0,
                                       loopback=dev(9, "Saída [Loopback]", 2, True))
    c = DispositivosWasapi().abrir("reuniao")
    assert [x.rotulo for x in c.capturas] == ["mic", "sistema"] and len(fluxos) == 2
    c.fechar()
    assert all(f.fechado for f in fluxos)


def test_sem_microfone(monkeypatch):
    _, estado = instalar_pyaudio_falso(monkeypatch, [dev(1, "Alto-falante [Loopback]", 2, True)])
    with pytest.raises(ErroCaptura) as e:
        DispositivosWasapi().abrir("ambiente")
    assert (e.value.mensagem, e.value.codigo) == (MSG_SEM_MIC, "sem_microfone") and estado["terminou"] == 1


def test_reuniao_sem_dispositivo_de_som_do_sistema(monkeypatch):
    fluxos, estado = instalar_pyaudio_falso(monkeypatch, [dev(0, "Mic A")], padrao_entrada=0, loopback=None)
    with pytest.raises(ErroCaptura) as e:
        DispositivosWasapi().abrir("reuniao")
    assert (e.value.mensagem, e.value.codigo) == (MSG_SEM_SISTEMA, "sem_sistema")
    assert estado["terminou"] == 1 and not fluxos             # nem chegou a abrir o microfone


def test_microfone_em_uso_ou_bloqueado(monkeypatch):
    _, estado = instalar_pyaudio_falso(monkeypatch, [dev(0, "Mic A")], padrao_entrada=0, falha_abrir=(0,))
    with pytest.raises(ErroCaptura) as e:
        DispositivosWasapi().abrir("ambiente")
    assert (e.value.mensagem, e.value.codigo) == (MSG_MIC_ABRIR, "mic_abrir") and estado["terminou"] == 1


def test_falha_no_loopback_fecha_o_microfone_ja_aberto(monkeypatch):
    fluxos, estado = instalar_pyaudio_falso(monkeypatch, [dev(0, "Mic A")], padrao_entrada=0,
                                            loopback=dev(9, "Saída", 2, True), falha_abrir=(9,))
    with pytest.raises(ErroCaptura) as e:
        DispositivosWasapi().abrir("reuniao")
    assert e.value.mensagem == MSG_SISTEMA_ABRIR
    assert len(fluxos) == 1 and fluxos[0].fechado and estado["terminou"] == 1


def test_captura_marca_inativa_quando_o_dispositivo_para(monkeypatch):
    fluxos, _ = instalar_pyaudio_falso(monkeypatch, [dev(0, "Mic A")], padrao_entrada=0)
    c = DispositivosWasapi().abrir("ambiente")
    assert c.capturas[0].ativa()
    fluxos[0].fechado = True
    assert not c.capturas[0].ativa()


# ---------- captura simulada ----------
def test_fonte_simulada_acompanha_o_relogio():
    t = [100.0]
    f = FonteSimulada("mic", relogio=lambda: t[0])
    assert len(f.coletar()) == 0                    # a primeira chamada só marca o tempo
    t[0] += 2.0
    x = f.coletar()
    assert len(x) == 32000 and x.dtype == np.float32 and 0 < np.abs(x).max() <= 0.3
    t[0] += 0.5
    assert len(f.coletar()) == 8000


def test_fonte_simulada_com_audio_repete_o_audio():
    t = [0.0]
    audio = np.linspace(0, 0.5, 100, dtype=np.float32)
    f = FonteSimulada("mic", audio, relogio=lambda: t[0])
    f.coletar()
    t[0] = 0.02                                      # 320 amostras: o áudio de 100 amostras se repete
    x = f.coletar()
    assert len(x) == 320 and np.allclose(x[:100], audio) and np.allclose(x[100:200], audio)


def test_dispositivos_simulados():
    d = DispositivosSimulados()
    assert d.suportado() == (True, "") and len(d.microfones()) == 2
    assert [c.rotulo for c in d.abrir("reuniao").capturas] == ["mic", "sistema"]
    assert [c.rotulo for c in d.abrir("ambiente").capturas] == ["mic"]
    sem = DispositivosSimulados(sem_microfone=True)
    assert sem.microfones() == []
    with pytest.raises(ErroCaptura):
        sem.abrir("ambiente")


def test_nivel_visual_vai_de_vazio_a_cheio():
    assert nivel_visual(np.zeros(0, np.float32)) == 0.0
    assert nivel_visual(np.zeros(100, np.float32)) == 0.0
    assert nivel_visual(np.full(100, 1.0, np.float32)) == 1.0
    assert 0.45 < nivel_visual(np.full(100, 0.03, np.float32)) < 0.55      # ≈ -30 dBFS
