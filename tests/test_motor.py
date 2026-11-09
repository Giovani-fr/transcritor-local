import numpy as np

from transcritor import motor
from transcritor.motor import Linha, formatar_tempo, normalizar_para_whisper


def test_formatar_tempo():
    assert formatar_tempo(0) == "00:00:00"
    assert formatar_tempo(3725.9) == "01:02:05"


def test_linha_str_com_e_sem_fundo():
    assert str(Linha(65, "oi")) == "[00:01:05] oi"
    assert str(Linha(65, "oi", fundo=True)) == "[00:01:05] (fundo) oi"


def test_normalizacao_silencio_absoluto_vira_none():
    assert normalizar_para_whisper(np.zeros(16000, np.float32)) is None
    assert normalizar_para_whisper(np.array([], np.float32)) is None


def test_normalizacao_sobe_volume_e_respeita_ganho_maximo():
    baixo = np.full(1600, 0.01, np.float32)             # 0.7/0.01 = 70x, limitado a 15x
    assert np.isclose(np.abs(normalizar_para_whisper(baixo)).max(), 0.15)
    medio = np.full(1600, 0.2, np.float32)               # 3,5x
    assert np.isclose(np.abs(normalizar_para_whisper(medio)).max(), 0.7)


def test_normalizacao_nao_altera_o_original():
    x = np.full(1600, 0.01, np.float32)
    normalizar_para_whisper(x)
    assert np.allclose(x, 0.01)


def test_parametros_que_evitam_loop_de_reticencias():
    """Guarda as lições do protótipo: VAD afinado e sem initial_prompt/contexto."""
    assert motor.VAD == dict(threshold=0.3, min_silence_duration_ms=400, speech_pad_ms=300)
    chamadas = {}

    class Falso:
        def transcribe(self, audio, **kw):
            chamadas.update(kw)
            return iter([]), None

    motor.processar_trecho(Falso(), np.full(16000, 0.1, np.float32), 0.0)
    assert chamadas["condition_on_previous_text"] is False
    assert "initial_prompt" not in chamadas
    assert chamadas["language"] == "pt" and chamadas["vad_filter"] is True
    assert chamadas["vad_parameters"] == motor.VAD


def test_processar_trecho_filtra_alucinacao_e_marca_fundo():
    class Seg:
        def __init__(self, a, b, t):
            self.start, self.end, self.text = a, b, t

    audio = np.zeros(16000 * 20, np.float32)
    for i in range(6):                       # 5 falas fortes + 1 fraca
        audio[i * 16000:(i + 1) * 16000] = 0.2 if i < 5 else 0.01

    class Falso:
        def transcribe(self, a, **kw):
            segs = [Seg(i, i + 1, f"fala {i}") for i in range(6)]
            segs.insert(2, Seg(7, 8, "Obrigado."))
            return iter(segs), None

    linhas = motor.processar_trecho(Falso(), audio, 100.0)
    assert [l.texto for l in linhas] == [f"fala {i}" for i in range(6)]
    assert [l.fundo for l in linhas] == [False] * 5 + [True]
    assert linhas[0].inicio == 100.0
