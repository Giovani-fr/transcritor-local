"""Serviço: gravação ao vivo e prévia, com dispositivos e modelo falsos (ninguém é gravado)."""
import json
import threading
import time
import wave

import numpy as np
import pytest

from transcritor import erros
from transcritor.captura import MSG_SEM_MIC, MSG_SEM_SISTEMA, ConjuntoCaptura, ErroCaptura
from transcritor.servico import NaoPronto, Ocupado, Servico
from transcritor.youtube import ErroYoutube

from test_servico import HW, ModeloFalso, esperar, wav_sintetico

SR = 16000


class Fonte:
    """Entrega 1 s de tom por chamada, até acabar `blocos`; depois fica em silêncio."""

    def __init__(self, rotulo, blocos=5, amp=0.3, ativa_ate=None):
        self.rotulo, self.blocos, self.amp, self.ativa_ate = rotulo, blocos, amp, ativa_ate
        self.chamadas, self.fechada = 0, False

    def coletar(self):
        self.chamadas += 1
        if self.blocos <= 0:
            return np.zeros(0, np.float32)
        self.blocos -= 1
        t = np.arange(SR) / SR
        return (np.sin(2 * np.pi * 220 * t) * self.amp).astype(np.float32)

    def ativa(self):
        return self.ativa_ate is None or self.chamadas < self.ativa_ate

    def fechar(self):
        self.fechada = True


class Dispositivos:
    def __init__(self, sem_mic=False, sem_sistema=False, **kw):
        self.sem_mic, self.sem_sistema, self.kw = sem_mic, sem_sistema, kw
        self.abertos: list[ConjuntoCaptura] = []
        self.pedidos: list[tuple] = []

    def suportado(self):
        return True, ""

    def microfones(self):
        return [{"id": "Mic A", "nome": "Mic A", "padrao": True}]

    def abrir(self, fonte, microfone=None):
        self.pedidos.append((fonte, microfone))
        if self.sem_mic:
            raise ErroCaptura(MSG_SEM_MIC, "sem_microfone")
        if fonte == "reuniao" and self.sem_sistema:
            raise ErroCaptura(MSG_SEM_SISTEMA, "sem_sistema")
        capturas = [Fonte("mic", **self.kw)] + ([Fonte("sistema", **self.kw)] if fonte == "reuniao" else [])
        c = ConjuntoCaptura(capturas)
        self.abertos.append(c)
        return c


def criar(tmp_path, disp=None, modelo=None, eventos=None):
    s = Servico(tmp_path / "saidas", HW, instalado=lambda n, c=None: True,
                carregar=lambda hw, c=None: (modelo or ModeloFalso(), hw),
                publicar=(lambda t, d: eventos.append((t, d))) if eventos is not None else None,
                dispositivos=disp or Dispositivos())
    s.iniciar()
    esperar(lambda: s.estado_modelo["fase"] == "pronto")
    return s


def terminar(s):
    esperar(lambda: s.trabalho["fase"] in ("concluido", "parado", "erro"))


def test_gravacao_completa_guarda_audio_e_texto(tmp_path):
    s = criar(tmp_path)
    id_ = s.iniciar_gravacao("ambiente", "Mic A")
    esperar(lambda: s.trabalho["fase"] == "gravando" and s.trabalho["gravado_s"] >= 4)
    s.parar()
    terminar(s)

    t = s.trabalho
    assert t["fase"] == "concluido" and t["tipo"] == "gravacao" and t["aviso"] == ""      # parar é o fim normal
    pasta = s.historico.caminho(id_)
    assert (pasta / "audio_001.wav").exists()
    with wave.open(str(pasta / "audio_001.wav")) as w:
        assert abs(w.getnframes() / SR - 5) < 0.2
    meta = json.loads((pasta / "meta.json").read_text(encoding="utf-8"))
    assert meta["tipo"] == "gravacao" and meta["fonte"] == "ambiente" and meta["audio_guardado"] is True
    assert meta["status"] == "concluido" and meta["linhas"] == 3 and abs(meta["duracao_s"] - 5) < 0.2
    assert s.texto(id_).startswith("[00:00:00] Primeira frase.")
    assert [h["id"] for h in s.historico.listar()] == [id_]


def test_reuniao_abre_microfone_e_som_do_sistema(tmp_path):
    disp = Dispositivos()
    s = criar(tmp_path, disp)
    s.iniciar_gravacao("reuniao", "")
    esperar(lambda: s.trabalho["fase"] == "gravando")
    assert [c.rotulo for c in disp.abertos[-1].capturas] == ["mic", "sistema"]
    s.parar()
    terminar(s)
    assert all(c.fechada for c in disp.abertos[-1].capturas)       # tudo desligado no fim
    assert s.trabalho["fonte"] == "reuniao"


def test_sem_microfone_mensagem_simples_e_nada_e_criado(tmp_path):
    s = criar(tmp_path, Dispositivos(sem_mic=True))
    with pytest.raises(ErroCaptura) as e:
        s.iniciar_gravacao("ambiente")
    assert e.value.mensagem == MSG_SEM_MIC and e.value.codigo == "sem_microfone"
    assert s.trabalho is None and not (tmp_path / "saidas").exists()


def test_sem_dispositivo_de_som_do_sistema(tmp_path):
    s = criar(tmp_path, Dispositivos(sem_sistema=True))
    with pytest.raises(ErroCaptura) as e:
        s.iniciar_gravacao("reuniao")
    assert e.value.codigo == "sem_sistema"
    s.iniciar_gravacao("ambiente")          # só o microfone ainda funciona
    s.parar()
    terminar(s)


def test_fonte_desconhecida_e_modelo_nao_pronto(tmp_path):
    s = criar(tmp_path)
    with pytest.raises(ValueError):
        s.iniciar_gravacao("lua")
    novo = Servico(tmp_path / "x", HW, instalado=lambda n, c=None: False, dispositivos=Dispositivos())
    with pytest.raises(NaoPronto):
        novo.iniciar_gravacao("ambiente")


def test_ocupado_enquanto_grava(tmp_path):
    s = criar(tmp_path)
    s.iniciar_gravacao("ambiente")
    esperar(lambda: s.trabalho["fase"] == "gravando")
    with pytest.raises(Ocupado):
        s.iniciar_gravacao("ambiente")
    with pytest.raises(Ocupado):
        s.iniciar_arquivo("x.wav", "x.wav")
    with pytest.raises(Ocupado):
        s.iniciar_previa("ambiente")
    s.parar()
    terminar(s)


def test_gravacao_sem_fala_avisa_mas_guarda_o_audio(tmp_path):
    s = criar(tmp_path, modelo=ModeloFalso(segs=[]))
    id_ = s.iniciar_gravacao("ambiente")
    esperar(lambda: s.trabalho["fase"] == "gravando" and s.trabalho["gravado_s"] >= 3)
    s.parar()
    terminar(s)
    assert s.trabalho["fase"] == "concluido" and "Não encontrei fala na gravação" in s.trabalho["aviso"]
    pasta = s.historico.caminho(id_)
    assert pasta is not None and (pasta / "audio_001.wav").exists()      # a pasta com o áudio não é apagada
    assert [h["id"] for h in s.historico.listar()] == [id_]


def test_dispositivo_some_no_meio_guarda_o_audio_e_explica(tmp_path):
    s = criar(tmp_path, Dispositivos(blocos=100, ativa_ate=3))
    id_ = s.iniciar_gravacao("ambiente")
    terminar(s)
    t = s.trabalho
    assert t["fase"] == "concluido" and "ficou salvo" in t["aviso"]
    assert (s.historico.caminho(id_) / "audio_001.wav").exists()


def test_erro_em_um_trecho_vira_aviso_no_texto_sem_traceback(tmp_path):
    class Quebrado(ModeloFalso):
        def transcribe(self, audio, **kw):
            raise RuntimeError("falha do modelo simulada")

    s = criar(tmp_path, modelo=Quebrado())
    id_ = s.iniciar_gravacao("ambiente")
    esperar(lambda: s.trabalho["gravado_s"] >= 3)
    s.parar()
    terminar(s)
    texto = s.texto(id_)
    assert "não consegui transcrever este trecho" in texto and "RuntimeError" not in texto
    assert "falha do modelo simulada" in (tmp_path / "saidas" / "registro.log").read_text(encoding="utf-8")
    assert s.trabalho["fase"] == "concluido"


def test_progresso_publicado_com_atraso_e_pendentes(tmp_path):
    eventos = []
    s = criar(tmp_path, eventos=eventos)
    s.iniciar_gravacao("ambiente")
    esperar(lambda: s.trabalho["gravado_s"] >= 4)
    assert {"atraso_s", "atrasado", "pendentes", "gravado_s", "fonte"} <= set(s.trabalho)
    s.parar()
    terminar(s)
    assert any(t == "nivel" and d["origem"] == "gravacao" and "mic" in d["niveis"] for t, d in eventos)
    assert s.trabalho["atrasado"] is False and s.trabalho["pendentes"] == 0


def test_parar_durante_a_espera_mostra_fase_parando(tmp_path):
    liberar = threading.Event()
    s = criar(tmp_path, modelo=ModeloFalso(liberar=liberar, atraso=0.0))
    s.iniciar_gravacao("ambiente")
    esperar(lambda: s.trabalho["gravado_s"] >= 4)
    s.parar()
    assert s.trabalho["fase"] == "parando"
    liberar.set()
    terminar(s)
    assert s.trabalho["fase"] == "concluido"


# ---------- prévia ----------
def test_previa_publica_nivel_e_reaproveita_a_mesma_captura(tmp_path):
    eventos, disp = [], Dispositivos(blocos=1000)
    s = criar(tmp_path, disp, eventos=eventos)
    s.iniciar_previa("ambiente", "Mic A")
    s.iniciar_previa("ambiente", "Mic A")          # renovação: não abre de novo
    assert len(disp.abertos) == 1
    esperar(lambda: any(t == "nivel" and d["origem"] == "previa" for t, d in eventos))
    s.iniciar_previa("reuniao", "Mic A")           # outra fonte: reabre
    assert len(disp.abertos) == 2 and disp.abertos[0].capturas[0].fechada
    s.parar_previa()
    assert disp.abertos[1].capturas[0].fechada


def test_iniciar_gravacao_desliga_a_previa(tmp_path):
    disp = Dispositivos(blocos=1000)
    s = criar(tmp_path, disp)
    s.iniciar_previa("ambiente")
    s.iniciar_gravacao("ambiente")
    assert disp.abertos[0].capturas[0].fechada and s._previa is None
    s.parar()
    terminar(s)


def test_previa_com_erro_de_dispositivo_levanta_mensagem_simples(tmp_path):
    s = criar(tmp_path, Dispositivos(sem_mic=True))
    with pytest.raises(ErroCaptura) as e:
        s.iniciar_previa("ambiente")
    assert e.value.mensagem == MSG_SEM_MIC


def test_erro_inesperado_do_dispositivo_vira_mensagem_generica(tmp_path):
    class Quebra(Dispositivos):
        def abrir(self, fonte, microfone=None):
            raise RuntimeError("detalhe técnico")

    s = criar(tmp_path, Quebra())
    with pytest.raises(ErroCaptura) as e:
        s.iniciar_gravacao("ambiente")
    assert "detalhe técnico" not in e.value.mensagem
    assert "detalhe técnico" in (tmp_path / "saidas" / "registro.log").read_text(encoding="utf-8")


def test_info_gravacao_lista_microfones_e_respeita_sistema_nao_suportado(tmp_path):
    s = criar(tmp_path)
    assert s.info_gravacao() == {"suportado": True, "motivo": "", "microfones": [
        {"id": "Mic A", "nome": "Mic A", "padrao": True}]}

    class Nao(Dispositivos):
        def suportado(self):
            return False, "só no Windows"

    assert criar(tmp_path / "b", Nao()).info_gravacao() == {"suportado": False, "motivo": "só no Windows",
                                                            "microfones": []}


def test_gravacao_interrompida_sem_texto_continua_no_historico(tmp_path):
    """Se o programa fechar no meio, o áudio salvo não pode sumir da lista."""
    s = criar(tmp_path)
    id_, pasta = s.historico.nova("Gravação de ambiente")
    s.historico.gravar_meta(pasta, {"nome": "Gravação de ambiente", "tipo": "gravacao", "status": "em_andamento"})
    item = [h for h in s.historico.listar() if h["id"] == id_]
    assert item and item[0]["status"] == "interrompido"


# ---------- atualizar o yt-dlp ----------
def test_atualizar_ytdlp_so_quando_nao_ha_trabalho(tmp_path):
    chamadas = []
    s = Servico(tmp_path / "s", HW, instalado=lambda n, c=None: True,
                carregar=lambda hw, c=None: (ModeloFalso(atraso=0.3), hw), dispositivos=Dispositivos(),
                atualizar_ytdlp=lambda: chamadas.append(1) or {"antes": "1", "depois": "2", "mudou": True,
                                                              "reiniciar": False})
    s.iniciar()
    esperar(lambda: s.estado_modelo["fase"] == "pronto")
    assert s.atualizar_ytdlp()["depois"] == "2" and chamadas == [1]
    s.iniciar_arquivo(wav_sintetico(tmp_path / "a.wav"), "a.wav")
    with pytest.raises(Ocupado):
        s.atualizar_ytdlp()
    assert chamadas == [1]
    s.parar()
    esperar(lambda: s.trabalho["fase"] in ("parado", "concluido"))


def test_durante_a_atualizacao_nao_comeca_outro_trabalho_e_erro_e_registrado(tmp_path):
    entrou, soltar = threading.Event(), threading.Event()

    def lento():
        entrou.set()
        soltar.wait(5)
        raise ErroYoutube("Não consegui atualizar.", "atualizacao")

    s = Servico(tmp_path / "s", HW, instalado=lambda n, c=None: True, atualizar_ytdlp=lento,
                carregar=lambda hw, c=None: (ModeloFalso(), hw), dispositivos=Dispositivos())
    s.iniciar()
    esperar(lambda: s.estado_modelo["fase"] == "pronto")
    resultado = []
    t = threading.Thread(target=lambda: resultado.append(_tentar(s)))
    t.start()
    entrou.wait(5)
    with pytest.raises(Ocupado):
        s.iniciar_gravacao("ambiente")
    soltar.set()
    t.join(5)
    assert resultado == ["Não consegui atualizar."]
    s.iniciar_gravacao("ambiente")                       # liberado de novo
    s.parar()
    esperar(lambda: s.trabalho["fase"] == "concluido")


def _tentar(s):
    try:
        s.atualizar_ytdlp()
    except ErroYoutube as e:
        return e.mensagem
