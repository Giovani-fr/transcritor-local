"""Gravação ao vivo com o Whisper de verdade, mas com fala sintética (voz do Windows) no lugar do microfone."""
import threading

import numpy as np
import pytest

from transcritor.captura import ConjuntoCaptura
from transcritor.gravacao import Gravacao

from test_gravacao import FonteFalsa

pytestmark = pytest.mark.modelo


def test_trechos_viram_texto_com_o_modelo_real(fala, modelo_cpu, tmp_path):
    audio, _ = fala
    audio = np.tile(audio, 3).astype(np.float32)                        # ≈ 15 s de fala sintética
    blocos = [audio[i:i + 16000] for i in range(0, len(audio), 16000)]  # 1 s por chamada, como no tempo real
    parar = threading.Event()
    linhas, erros = [], []
    g = Gravacao(ConjuntoCaptura([FonteFalsa("mic", blocos, parar)]), str(tmp_path / "g"), modelo_cpu, parar,
                 ao_linha=linhas.append, ao_erro=lambda t0, e: erros.append(e), ao_nivel=lambda o, n: None,
                 ao_progresso=lambda *a: None, trecho_s=8, passo_s=0)
    resultado = g.executar()
    texto = " ".join(l.texto for l in linhas).lower()
    assert not erros and resultado.falha is None
    assert "cliente" in texto and "sistema" in texto, texto
    assert len(list((tmp_path / "g").glob("*.wav"))) == 1               # áudio original guardado
    assert [l.inicio for l in linhas] == sorted(l.inicio for l in linhas)  # horários em ordem, somando o início de cada trecho
