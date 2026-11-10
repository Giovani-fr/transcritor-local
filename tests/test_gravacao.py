"""Gravação ao vivo com captura simulada: nada aqui usa microfone nem grava pessoas."""
import threading
import time
import wave

import numpy as np

from transcritor.captura import ConjuntoCaptura, MedidorNivel
from transcritor.gravacao import ATRASO_AVISO_S, Gravacao, Previa
from transcritor.motor import Linha

SR = 16000


class FonteFalsa:
    """Entrega um bloco de áudio por chamada; quando acaba, pede para parar a gravação."""

    def __init__(self, rotulo, blocos, parar=None, ativa_ate=None):
        self.rotulo, self.blocos, self.parar = rotulo, list(blocos), parar
        self.ativa_ate, self.chamadas, self.fechada = ativa_ate, 0, False

    def coletar(self):
        self.chamadas += 1
        if self.blocos:
            return self.blocos.pop(0)
        if self.parar is not None:
            self.parar.set()
        return np.zeros(0, np.float32)

    def ativa(self):
        return self.ativa_ate is None or self.chamadas < self.ativa_ate

    def fechar(self):
        self.fechada = True


def tom(segundos, amp=0.3, freq=220):
    t = np.arange(int(segundos * SR)) / SR
    return (np.sin(2 * np.pi * freq * t) * amp).astype(np.float32)


def segundos(n, amp=0.3):
    return [tom(1, amp) for _ in range(n)]


class Registro:
    def __init__(self):
        self.linhas, self.erros, self.progresso, self.niveis, self.trechos = [], [], [], [], []

    def processar(self, modelo, audio, t0, detector):
        self.trechos.append((t0, len(audio) / SR))
        return [Linha(t0, f"trecho em {int(t0)}")]


def montar(tmp_path, fontes, reg=None, parar=None, processar=None, **kw):
    reg = reg or Registro()
    parar = parar or threading.Event()
    for f in fontes:
        if f.parar is None:
            f.parar = parar
    conjunto = ConjuntoCaptura(list(fontes))
    g = Gravacao(conjunto, str(tmp_path / "grav"), None, parar,
                 ao_linha=reg.linhas.append, ao_erro=lambda t0, e: reg.erros.append((t0, e)),
                 ao_nivel=lambda origem, n: reg.niveis.append((origem, n)),
                 ao_progresso=lambda *a: reg.progresso.append(a),
                 processar=processar or reg.processar, passo_s=0, **kw)
    return g, reg, conjunto


def duracao_wavs(pasta):
    total, nomes = 0, []
    for p in sorted(pasta.glob("*.wav")):
        with wave.open(str(p)) as w:
            total += w.getnframes()
        nomes.append(p.name)
    return total / SR, nomes


def test_trechos_de_30s_e_sobra_final(tmp_path):
    g, reg, conjunto = montar(tmp_path, [FonteFalsa("mic", segundos(70))])
    r = g.executar()
    assert r.falha is None and abs(r.gravado_s - 70) < 0.01
    assert reg.trechos == [(0.0, 30.0), (30.0, 30.0), (60.0, 10.0)]     # a sobra de 10 s também é transcrita
    assert [l.texto for l in reg.linhas] == ["trecho em 0", "trecho em 30", "trecho em 60"]
    assert conjunto.capturas[0].fechada                                  # microfone desligado ao terminar
    assert duracao_wavs(tmp_path / "grav")[0] == 70


def test_sobra_minuscula_nao_e_transcrita(tmp_path):
    g, reg, _ = montar(tmp_path, [FonteFalsa("mic", segundos(30) + [tom(0.2)])])
    g.executar()
    assert reg.trechos == [(0.0, 30.0)]


def test_audio_original_em_blocos(tmp_path):
    g, reg, _ = montar(tmp_path, [FonteFalsa("mic", segundos(50))], bloco_s=20)
    g.executar()
    total, nomes = duracao_wavs(tmp_path / "grav")
    assert nomes == ["audio_001.wav", "audio_002.wav", "audio_003.wav"] and abs(total - 50) < 0.01


def test_reuniao_mistura_sistema_e_microfone(tmp_path):
    mic = FonteFalsa("mic", [tom(1, 0.2, 220)])
    sis = FonteFalsa("sistema", [tom(1, 0.3, 330)])
    g, reg, _ = montar(tmp_path, [mic, sis])
    g.executar()
    with wave.open(str(tmp_path / "grav" / "audio_001.wav")) as w:
        x = np.frombuffer(w.readframes(w.getnframes()), np.int16) / 32768.0
    assert len(x) == SR and 0.45 < np.abs(x).max() <= 0.51          # soma dos dois, sem passar de 1
    origens = {n for o, niv in reg.niveis for n in niv}
    assert origens == {"mic", "sistema"}


def test_dispositivo_que_some_encerra_e_guarda_o_que_foi_gravado(tmp_path):
    mic = FonteFalsa("mic", segundos(40), ativa_ate=36)       # "desconecta" depois de ~35 s
    g, reg, conjunto = montar(tmp_path, [mic])
    r = g.executar()
    assert r.falha is not None and r.falha.codigo == "perdeu" and "ficou salvo" in r.falha.mensagem
    assert 34 <= r.gravado_s <= 36
    assert reg.trechos[0] == (0.0, 30.0) and len(reg.trechos) == 2    # o que veio antes da falha foi transcrito
    assert conjunto.capturas[0].fechada and duracao_wavs(tmp_path / "grav")[0] >= 34


def test_erro_em_um_trecho_nao_derruba_a_gravacao(tmp_path):
    def processar(modelo, audio, t0, detector):
        if t0 == 30.0:
            raise ValueError("falha simulada")
        return [Linha(t0, f"ok {int(t0)}")]

    g, reg, _ = montar(tmp_path, [FonteFalsa("mic", segundos(70))], processar=processar)
    g.executar()
    assert [l.texto for l in reg.linhas] == ["ok 0", "ok 60"]
    assert len(reg.erros) == 1 and reg.erros[0][0] == 30.0
    assert duracao_wavs(tmp_path / "grav")[0] == 70


def test_atraso_da_transcricao_e_indicado(tmp_path):
    liberar = threading.Event()

    def lento(modelo, audio, t0, detector):
        liberar.wait(10)
        return []

    g, reg, _ = montar(tmp_path, [FonteFalsa("mic", segundos(125))], processar=lento)
    t = threading.Thread(target=lambda: g.executar())
    t.start()
    inicio = time.time()
    while time.time() - inicio < 10 and not any(a[1] > ATRASO_AVISO_S for a in reg.progresso):
        time.sleep(0.01)
    atrasos = [a for a in reg.progresso if a[1] > ATRASO_AVISO_S]
    assert atrasos, "o atraso deveria ter sido informado"
    gravado_s, atraso_s, pendentes = atrasos[0]
    assert atraso_s > ATRASO_AVISO_S and pendentes >= 1
    liberar.set()
    t.join(10)
    assert not t.is_alive() and g.atraso_s == 0 and g.trabalhador.pendentes == 0


def test_sem_atraso_quando_a_transcricao_acompanha(tmp_path):
    g, reg, _ = montar(tmp_path, [FonteFalsa("mic", segundos(65))])
    g.executar()
    assert g.atraso_s == 0
    assert all(a[1] <= 65 for a in reg.progresso)


# ---------- prévia (só mede o nível) ----------
def test_previa_mede_sem_gravar_e_desliga(tmp_path):
    fonte = FonteFalsa("mic", [tom(0.1, 0.4) for _ in range(5000)])
    niveis = []
    p = Previa(ConjuntoCaptura([fonte]), lambda o, n: niveis.append((o, n)), passo_s=0.01)
    inicio = time.time()
    while time.time() - inicio < 5 and len(niveis) < 5:
        time.sleep(0.01)
    assert niveis and niveis[0][0] == "previa" and niveis[0][1]["mic"]["n"] > 0.5
    p.parar()
    assert fonte.fechada and not p.viva
    assert list(tmp_path.iterdir()) == []                      # nada foi escrito em disco


def test_previa_desliga_sozinha_sem_renovacao():
    fonte = FonteFalsa("mic", [])
    p = Previa(ConjuntoCaptura([fonte]), lambda o, n: None, passo_s=0.01, ociosa_s=0.1)
    p.thread.join(5)
    assert not p.thread.is_alive() and fonte.fechada


def test_previa_renovada_continua_ligada():
    fonte = FonteFalsa("mic", [])
    p = Previa(ConjuntoCaptura([fonte]), lambda o, n: None, passo_s=0.01, ociosa_s=0.3)
    for _ in range(8):
        time.sleep(0.1)
        p.renovar()
    assert p.viva
    p.parar()
    assert fonte.fechada


# ---------- medidor ----------
def test_medidor_avisa_som_baixo_e_mudo_so_depois_de_alguns_segundos():
    m = MedidorNivel(passo_s=1, janela_s=6, minimo_s=3)
    baixo = tom(1, 0.01)
    assert [m.registrar(baixo)["aviso"] for _ in range(2)] == ["", ""]      # ainda cedo
    assert m.registrar(baixo)["aviso"] == "baixo"
    m2 = MedidorNivel(passo_s=1, janela_s=6, minimo_s=3)
    assert [m2.registrar(np.zeros(0, np.float32))["aviso"] for _ in range(3)][-1] == "mudo"
    m3 = MedidorNivel(passo_s=1, janela_s=6, minimo_s=3)
    assert all(m3.registrar(tom(1, 0.2))["aviso"] == "" for _ in range(5))   # volume normal: sem aviso


def test_medidor_aviso_some_quando_o_som_volta():
    m = MedidorNivel(passo_s=1, janela_s=4, minimo_s=2)
    for _ in range(4):
        m.registrar(tom(1, 0.005))
    assert m.registrar(tom(1, 0.005))["aviso"] == "baixo"
    for _ in range(4):
        r = m.registrar(tom(1, 0.3))
    assert r["aviso"] == "" and r["n"] > 0.6          # a janela de 4 s já só tem som alto
