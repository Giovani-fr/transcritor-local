import wave

import numpy as np

from transcritor.captura import GravadorWav, misturar, para_16k_mono


def test_wav_legivel_sem_fechar_o_arquivo(tmp_path):
    """Simula a janela sendo fechada de repente: o cabeçalho precisa estar válido."""
    g = GravadorWav(str(tmp_path))
    g.escrever(np.zeros(16000, np.float32))
    with wave.open(str(tmp_path / "audio_001.wav")) as w:   # arquivo ainda aberto pelo gravador
        assert w.getnframes() == 16000 and w.getframerate() == 16000 and w.getnchannels() == 1
    g.fechar()


def test_wav_em_blocos(tmp_path):
    g = GravadorWav(str(tmp_path), bloco_s=2)
    for _ in range(5):
        g.escrever(np.zeros(16000, np.float32))     # 5 s com blocos de 2 s
    g.fechar()
    nomes = sorted(p.name for p in tmp_path.glob("*.wav"))
    assert nomes == ["audio_001.wav", "audio_002.wav", "audio_003.wav"]


def test_para_16k_mono_estereo_48k():
    t = np.arange(48000) / 48000
    mono = (np.sin(2 * np.pi * 440 * t) * 0.5 * 32767).astype(np.int16)
    estereo = np.repeat(mono[:, None], 2, axis=1).tobytes()
    out = para_16k_mono(estereo, 2, 48000)
    assert out.dtype == np.float32 and abs(len(out) - 16000) <= 1
    assert 0.45 < np.abs(out).max() < 0.55


def test_misturar_tamanhos_diferentes_e_limita():
    m = misturar([np.full(10, 0.8, np.float32), np.full(5, 0.8, np.float32)])
    assert len(m) == 10 and m[0] == 1.0 and np.isclose(m[9], 0.8)
    assert len(misturar([])) == 0
