"""Captura de áudio no Windows (WASAPI loopback + microfone) e gravação em blocos de .wav."""
from __future__ import annotations

import os
import sys
import threading
import time
import wave
from collections import deque
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

    def ativa(self) -> bool:
        """False se o dispositivo parou de responder (ex.: o fone foi desconectado)."""
        try:
            return bool(self.stream.is_active())
        except Exception:  # noqa: BLE001
            return False

    def fechar(self) -> None:
        try:
            self.stream.stop_stream()
        finally:
            self.stream.close()


# ---------- erros em linguagem simples ----------
MSG_NAO_WINDOWS = ("A gravação ao vivo só está disponível no Windows. "
                   "As abas Arquivo e YouTube funcionam em qualquer sistema.")
MSG_SEM_COMPONENTE = ("Falta um componente para gravar neste computador (PyAudioWPatch). "
                      "Instale de novo as dependências com: pip install -r requirements.txt")
MSG_SEM_MIC = ("Não encontrei nenhum microfone. Conecte um microfone (ou um fone com microfone) "
               "e atualize a página.")
MSG_SEM_SISTEMA = ("Não encontrei o dispositivo de som do computador (alto-falante ou fone) para captar a "
                   "reunião. Confira se há uma saída de som ativa no Windows.")
MSG_MIC_ABRIR = ("Não consegui usar o microfone. Confira se o Windows deixa os programas usarem o microfone "
                 "(Configurações > Privacidade e segurança > Microfone) e se outro programa não o está usando.")
MSG_SISTEMA_ABRIR = ("Não consegui captar o som do computador. Tente de novo; se continuar, escolha outra "
                     "saída de som no Windows.")
MSG_PERDEU = ("O microfone ou a saída de som parou de responder (foi desconectado?). "
              "O que já foi gravado ficou salvo.")
MSG_CAPTURA = "Não consegui começar a captar o som. Tente de novo."


class ErroCaptura(Exception):
    """Erro já traduzido para o usuário. ``codigo`` deixa a interface reagir."""

    def __init__(self, mensagem: str, codigo: str):
        super().__init__(mensagem)
        self.mensagem, self.codigo = mensagem, codigo


# ---------- medidor de nível ----------
PICO_BAIXO = 0.02      # pico abaixo disso (≈ -34 dBFS) por alguns segundos: som baixo demais
PICO_MUDO = 0.002      # abaixo disso (≈ -54 dBFS): praticamente nada


def nivel_visual(audio: np.ndarray) -> float:
    """Volume de 0 a 1 para a barra: RMS em dB, de -60 dBFS (vazio) a 0 dBFS (cheio)."""
    if len(audio) == 0:
        return 0.0
    rms = float(np.sqrt(np.mean(np.square(audio, dtype=np.float64))))
    db = 20.0 * np.log10(max(rms, 1e-5))
    return float(min(max((db + 60.0) / 60.0, 0.0), 1.0))


class MedidorNivel:
    """Barra de volume + aviso de som baixo, olhando os últimos ``janela_s`` segundos.

    Chame ``registrar`` uma vez por passo (mesmo quando não chegou áudio). O aviso só aparece
    depois de ``minimo_s`` segundos de leitura, para não piscar no começo.
    """

    def __init__(self, passo_s: float = 0.1, janela_s: float = 6.0, minimo_s: float = 3.0):
        self.picos: deque[float] = deque(maxlen=max(1, round(janela_s / passo_s)))
        self.minimo = max(1, round(minimo_s / passo_s))

    def registrar(self, audio: np.ndarray) -> dict:
        self.picos.append(float(np.abs(audio).max()) if len(audio) else 0.0)
        aviso = ""
        if len(self.picos) >= self.minimo:
            pico = max(self.picos)
            aviso = "mudo" if pico < PICO_MUDO else "baixo" if pico < PICO_BAIXO else ""
        return {"n": round(nivel_visual(audio), 3), "aviso": aviso}


# ---------- conjuntos de captura e dispositivos ----------
class ConjuntoCaptura:
    """As fontes abertas para uma gravação (ou prévia) e o jeito de fechar tudo."""

    def __init__(self, capturas: list, ao_fechar=None):
        self.capturas = capturas
        self._ao_fechar = ao_fechar
        self._fechado = False

    def fechar(self) -> None:
        if self._fechado:
            return
        self._fechado = True
        for c in self.capturas:
            try:
                c.fechar()
            except Exception:  # noqa: BLE001 - fechar nunca pode causar outro erro
                pass
        if self._ao_fechar:
            try:
                self._ao_fechar()
            except Exception:  # noqa: BLE001
                pass


class DispositivosWasapi:
    """Dispositivos reais do Windows (WASAPI): microfones e o som do sistema (loopback)."""

    def suportado(self) -> tuple[bool, str]:
        if sys.platform != "win32":
            return False, MSG_NAO_WINDOWS
        try:
            import pyaudiowpatch  # noqa: F401
        except Exception:  # noqa: BLE001
            return False, MSG_SEM_COMPONENTE
        return True, ""

    def microfones(self) -> list[dict]:
        import pyaudiowpatch as pyaudio
        pa = pyaudio.PyAudio()
        try:
            host = pa.get_host_api_info_by_type(pyaudio.paWASAPI)
            padrao = host.get("defaultInputDevice", -1)
            vistos, saida = set(), []
            for i in range(host["deviceCount"]):
                d = pa.get_device_info_by_host_api_device_index(host["index"], i)
                if d["maxInputChannels"] > 0 and not d.get("isLoopbackDevice") and d["name"] not in vistos:
                    vistos.add(d["name"])
                    saida.append({"id": d["name"], "nome": d["name"], "padrao": d["index"] == padrao})
            return saida
        except OSError:
            return []
        finally:
            pa.terminate()

    def abrir(self, fonte: str, microfone: str | None = None) -> ConjuntoCaptura:
        """Abre o microfone (e, na reunião, o som do sistema). Levanta ErroCaptura."""
        ok, motivo = self.suportado()
        if not ok:
            raise ErroCaptura(motivo, "nao_suportado")
        import pyaudiowpatch as pyaudio
        pa = pyaudio.PyAudio()
        abertas: list[Captura] = []
        try:
            dev_mic = self._achar_microfone(pa, pyaudio, microfone)
            dev_sis = self._achar_loopback(pa) if fonte == "reuniao" else None
            for dev, rotulo, msg in ((dev_mic, "mic", MSG_MIC_ABRIR), (dev_sis, "sistema", MSG_SISTEMA_ABRIR)):
                if dev is None:
                    continue
                try:
                    abertas.append(Captura(pa, dev, rotulo))
                except (OSError, ValueError) as e:
                    raise ErroCaptura(msg, "mic_abrir" if rotulo == "mic" else "sistema_abrir") from e
        except BaseException:
            for c in abertas:
                try:
                    c.fechar()
                except Exception:  # noqa: BLE001
                    pass
            pa.terminate()
            raise
        return ConjuntoCaptura(abertas, pa.terminate)

    @staticmethod
    def _achar_microfone(pa, pyaudio, nome: str | None) -> dict:
        try:
            host = pa.get_host_api_info_by_type(pyaudio.paWASAPI)
            candidatos = []
            for i in range(host["deviceCount"]):
                d = pa.get_device_info_by_host_api_device_index(host["index"], i)
                if d["maxInputChannels"] > 0 and not d.get("isLoopbackDevice"):
                    candidatos.append(d)
            padrao = host.get("defaultInputDevice", -1)
        except OSError:
            candidatos, padrao = [], -1
        if not candidatos:
            raise ErroCaptura(MSG_SEM_MIC, "sem_microfone")
        if nome:
            for d in candidatos:
                if d["name"] == nome:
                    return d
        for d in candidatos:
            if d["index"] == padrao:
                return d
        return candidatos[0]

    @staticmethod
    def _achar_loopback(pa) -> dict:
        try:
            return pa.get_default_wasapi_loopback()
        except (OSError, LookupError) as e:
            raise ErroCaptura(MSG_SEM_SISTEMA, "sem_sistema") from e


# ---------- captura simulada (testes, demonstração e prints: nada vem de microfone) ----------
class FonteSimulada:
    """Gera áudio sintético em tempo real, no mesmo formato de ``Captura``."""

    def __init__(self, rotulo: str, audio: np.ndarray | None = None, amplitude: float | None = None,
                 relogio=time.monotonic):
        self.rotulo, self.audio = rotulo, audio
        self.amplitude = amplitude if amplitude is not None else (1.0 if audio is not None else 0.3)
        self._relogio, self._ultimo, self._pos = relogio, None, 0

    def _sinal(self, ini: int, n: int) -> np.ndarray:
        idx = ini + np.arange(n)
        if self.audio is not None and len(self.audio):
            return (self.audio[idx % len(self.audio)] * self.amplitude).astype(np.float32)
        t = idx / TARGET_SR
        rajadas = np.clip(np.sin(2 * np.pi * 0.4 * t) * 1.6, 0, 1) * (0.65 + 0.35 * np.sin(2 * np.pi * 3.1 * t))
        voz = np.sin(2 * np.pi * 140 * t) + 0.5 * np.sin(2 * np.pi * 280 * t) + 0.25 * np.sin(2 * np.pi * 420 * t)
        return (voz / 1.75 * rajadas * self.amplitude).astype(np.float32)

    def coletar(self) -> np.ndarray:
        agora = self._relogio()
        if self._ultimo is None:
            self._ultimo = agora
            return np.zeros(0, np.float32)
        n = int((agora - self._ultimo) * TARGET_SR)
        self._ultimo += n / TARGET_SR
        x = self._sinal(self._pos, n)
        self._pos += n
        return x

    def ativa(self) -> bool:
        return True

    def fechar(self) -> None:
        pass


class DispositivosSimulados:
    """Mesma interface de ``DispositivosWasapi``, com sinais sintéticos."""

    def __init__(self, audio_mic=None, audio_sistema=None, amp_mic=None, amp_sistema=None,
                 sem_microfone: bool = False):
        self.audio_mic, self.audio_sistema = audio_mic, audio_sistema
        self.amp_mic, self.amp_sistema = amp_mic, amp_sistema
        self.sem_microfone = sem_microfone

    def suportado(self) -> tuple[bool, str]:
        return True, ""

    def microfones(self) -> list[dict]:
        if self.sem_microfone:
            return []
        return [{"id": "Microfone de demonstração", "nome": "Microfone de demonstração", "padrao": True},
                {"id": "Fone com microfone (demonstração)", "nome": "Fone com microfone (demonstração)",
                 "padrao": False}]

    def abrir(self, fonte: str, microfone: str | None = None) -> ConjuntoCaptura:
        if self.sem_microfone:
            raise ErroCaptura(MSG_SEM_MIC, "sem_microfone")
        capturas = [FonteSimulada("mic", self.audio_mic, self.amp_mic)]
        if fonte == "reuniao":
            capturas.append(FonteSimulada("sistema", self.audio_sistema, self.amp_sistema))
        return ConjuntoCaptura(capturas)
