"""Fixtures dos testes. Todo áudio é sintético: silêncio, ruído, tom ou fala gerada por TTS local."""
import subprocess
import sys
import wave

import numpy as np
import pytest

from transcritor.captura import para_16k_mono


@pytest.fixture
def rng():
    return np.random.default_rng(1)


def _tts(texto: str, destino) -> bool:
    """Gera fala pt-BR com a voz do Windows (SAPI). Devolve False se indisponível."""
    if sys.platform != "win32":
        return False
    ps = ("Add-Type -AssemblyName System.Speech;"
          "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;"
          "$v = $s.GetInstalledVoices() | Where-Object { $_.VoiceInfo.Culture.Name -eq 'pt-BR' } | Select-Object -First 1;"
          "if (-not $v) { exit 3 };"
          "$s.SelectVoice($v.VoiceInfo.Name);"
          f"$s.SetOutputToWaveFile('{destino}'); $s.Speak('{texto}'); $s.Dispose()")
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, timeout=60)
    return r.returncode == 0


@pytest.fixture(scope="session")
def fala(tmp_path_factory):
    """Fala sintética em português (16 kHz mono float32) e o caminho do .wav."""
    destino = tmp_path_factory.mktemp("tts") / "fala.wav"
    texto = "Bom dia. Hoje vamos aprender a cadastrar um cliente novo no sistema."
    if not _tts(texto, destino):
        pytest.skip("sem voz pt-BR do Windows para gerar fala sintética")
    with wave.open(str(destino)) as w:
        raw, canais, taxa = w.readframes(w.getnframes()), w.getnchannels(), w.getframerate()
    return para_16k_mono(raw, canais, taxa), destino


@pytest.fixture(scope="session")
def modelo_cpu():
    """Modelo `small` em CPU (o padrão do app sem GPU), só para testes de integração."""
    from transcritor.hardware import Hardware
    from transcritor.motor import carregar_modelo
    try:
        modelo, _ = carregar_modelo(Hardware(False, "small", "cpu", "int8"))
    except Exception as e:  # sem internet na 1ª vez, por exemplo
        pytest.skip(f"modelo small indisponível: {e!r}")
    return modelo
