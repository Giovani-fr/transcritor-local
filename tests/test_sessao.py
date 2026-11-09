import numpy as np

from transcritor.motor import Linha
from transcritor.sessao import TrabalhadorTranscricao


def test_thread_nao_morre_e_registra_o_erro():
    saida = []

    def processar(modelo, audio, t0, detector):
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
