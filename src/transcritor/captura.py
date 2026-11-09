"""Captura de áudio no Windows (WASAPI loopback + microfone) e gravação em blocos de .wav."""
from __future__ import annotations

import os
import threading
import wave
from math import gcd

import numpy as np

from .motor import TARGET_SR

WAV_BLOCK_S = 10 * 60   # um arquivo de áudio a cada 10 min


def para_16k_mono(raw: bytes, canais: int, taxa: int) -> np.ndarray:
    """int16 intercalado → float32 mono 16 kHz."""
    x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if canais > 1:
        x = x.reshape(-1, canais).mean(axis=1)
    if taxa != TARGET_SR:
        from scipy.signal import resample_poly
        g = gcd(taxa, TARGET_SR)
        x = resample_poly(x, TARGET_SR // g, taxa // g).astype(np.float32)
    return x


def misturar(partes: list[np.ndarray]) -> np.ndarray:
    """Soma várias fontes (tamanhos diferentes) e limita a [-1, 1]."""
    n = max((len(p) for p in partes), default=0)
    mix = np.zeros(n, np.float32)
    for p in partes:
        mix[:len(p)] += p
    return np.clip(mix, -1.0, 1.0)


class GravadorWav:
    """Grava .wav (16 kHz mono) em blocos de 10 min.

    O cabeçalho é corrigido e o arquivo descarregado a cada trecho, então o áudio
    continua legível mesmo se a janela for fechada de repente.
    """

    def __init__(self, pasta: str, bloco_s: float = WAV_BLOCK_S):
        self.pasta, self.bloco_s = pasta, bloco_s
        self.n = 0
        self._wav = None
        self._amostras = 0
        os.makedirs(pasta, exist_ok=True)
        self._abrir()

    def _abrir(self) -> None:
        if self._wav:
            self._wav.close()
        self.n += 1
        self._wav = wave.open(os.path.join(self.pasta, f"audio_{self.n:03d}.wav"), "wb")
        self._wav.setnchannels(1)
        self._wav.setsampwidth(2)
        self._wav.setframerate(TARGET_SR)
        self._amostras = 0

    def escrever(self, audio: np.ndarray) -> None:
        self._wav.writeframes((np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes())
        try:
            self._wav._patchheader()
            self._wav._file.flush()
        except Exception:
            pass
        self._amostras += len(audio)
        if self._amostras >= self.bloco_s * TARGET_SR:
            self._abrir()

    def fechar(self) -> None:
        if self._wav:
            self._wav.close()
            self._wav = None


class Captura:
    """Abre um dispositivo WASAPI (microfone ou loopback) e acumula amostras 16 kHz mono."""

    def __init__(self, pa, dev: dict, rotulo: str):
        import pyaudiowpatch as pyaudio
        self._pyaudio = pyaudio
        self.rotulo = rotulo
        self.buf: list[np.ndarray] = []
        self.lock = threading.Lock()
        self.canais = int(dev["maxInputChannels"])
        self.taxa = int(dev["defaultSampleRate"])
        self.stream = pa.open(
            format=pyaudio.paInt16, channels=self.canais, rate=self.taxa, input=True,
            input_device_index=dev["index"], frames_per_buffer=int(self.taxa * 0.1),
            stream_callback=self._cb)

    def _cb(self, in_data, frame_count, time_info, status):
        x = para_16k_mono(in_data, self.canais, self.taxa)
        with self.lock:
            self.buf.append(x)
        return (None, self._pyaudio.paContinue)

    def coletar(self) -> np.ndarray:
        with self.lock:
            out = np.concatenate(self.buf) if self.buf else np.zeros(0, np.float32)
            self.buf = []
        return out

    def fechar(self) -> None:
        self.stream.stop_stream()
        self.stream.close()
