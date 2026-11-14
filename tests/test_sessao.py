import numpy as np

from transcritor.motor import Linha
from transcritor.sessao import TrabalhadorTranscricao


def test_thread_nao_morre_e_registra_o_erro():
    saida = []

    def processar(modelo, audio, t0, detector, idioma="pt"):
        if t0 == 30.0:
            raise ValueError("falha simulada")
        return [Linha(t0, f"trecho {int(t0)}")]

    t = TrabalhadorTranscricao(None, saida.append, processar)
    t.iniciar()
    for t0 in (0.0, 30.0, 60.0):
        t.enviar(t0, np.zeros(10, np.float32))
    t.finalizar(timeout=5)
    assert saida[0] == "[00:00:00] trecho 0"
    assert saida[1].startswith("[00:00:30]") and "ERRO na transcrição" in saida[1] and "falha simulada" in saida[1]
    assert saida[2] == "[00:01:00] trecho 60"      # continuou depois do erro
    assert not t.thread.is_alive()


def test_idioma_vai_por_palavra_chave_mesmo_com_outro_parametro_antes_dele():
    """O idioma nunca é posicional: um parâmetro a mais no ``processar`` não pode receber o idioma por engano."""
    vistos = []

    def processar(modelo, audio, t0, detector, extra="nada", *, idioma):
        vistos.append((extra, idioma))
        return []

    for idioma in ("pt", None):
        t = TrabalhadorTranscricao(None, processar=processar, idioma=idioma)
        t.iniciar()
        t.enviar(0.0, np.zeros(10, np.float32))
        t.finalizar(timeout=5)
    assert vistos == [("nada", "pt"), ("nada", None)]


def test_idioma_padrao_e_portugues():
    vistos = []

    def processar(modelo, audio, t0, detector, idioma):
        vistos.append(idioma)
        return []

    t = TrabalhadorTranscricao(None, processar=processar)
    t.iniciar()
    t.enviar(0.0, np.zeros(10, np.float32))
    t.finalizar(timeout=5)
    assert vistos == ["pt"]
