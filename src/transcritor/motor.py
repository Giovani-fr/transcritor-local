"""Motor de transcrição (faster-whisper): VAD, normalização e marcação de (fundo).

Lições preservadas do protótipo:
- sem ``initial_prompt`` e com ``condition_on_previous_text=False`` (reticências no
  contexto faziam o modelo entrar em loop de "...");
- volume normalizado só para o Whisper (ganho máx. 15x); o áudio salvo fica original;
- VAD ``threshold=0.3``, ``min_silence_duration_ms=400``, ``speech_pad_ms=300``.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable, Iterator

import numpy as np

from .filtros import DetectorFundo, eh_alucinacao
from .hardware import Hardware, detectar_hardware, preparar_cuda

TARGET_SR = 16000
PICO_SILENCIO = 0.0008
PICO_ALVO = 0.7
GANHO_MAX = 15.0
VAD = dict(threshold=0.3, min_silence_duration_ms=400, speech_pad_ms=300)


@dataclass(frozen=True)
class Linha:
    inicio: float          # segundos desde o começo da gravação/arquivo
    texto: str
    fundo: bool = False

    def __str__(self) -> str:
        marca = " (fundo)" if self.fundo else ""
        return f"[{formatar_tempo(self.inicio)}]{marca} {self.texto}"


def formatar_tempo(segundos: float) -> str:
    s = int(segundos)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def normalizar_para_whisper(audio: np.ndarray) -> np.ndarray | None:
    """Sobe o volume (ganho máx. 15x). Devolve None se for silêncio absoluto."""
    pico = float(np.abs(audio).max()) if len(audio) else 0.0
    if pico < PICO_SILENCIO:
        return None
    ganho = min(PICO_ALVO / pico, GANHO_MAX)
    return np.clip(audio * ganho, -1.0, 1.0).astype(np.float32)


def _abrir_whisper(modelo: str, device: str, compute_type: str, cpu_threads: int, cache_dir: str | None):
    """Abre o WhisperModel SEM falar com a internet quando o modelo já está no PC.

    Sem ``local_files_only`` o faster-whisper consulta o huggingface.co a cada abertura (só metadados, mas é rede).
    Se o modelo ainda não está completo no PC, cai para o modo normal, que baixa.
    """
    from faster_whisper import WhisperModel
    comum = dict(device=device, compute_type=compute_type, cpu_threads=cpu_threads, download_root=cache_dir)
    try:
        return WhisperModel(modelo, local_files_only=True, **comum)
    except Exception:   # noqa: BLE001 - não estava no PC (ou o arquivo local falhou): tenta do jeito normal
        return WhisperModel(modelo, **comum)


def carregar_modelo(hw: Hardware | None = None, cache_dir: str | None = None):
    """Carrega o WhisperModel. Se a GPU falhar, cai para CPU. Devolve (modelo, hardware usado)."""
    hw = hw or detectar_hardware()
    preparar_cuda()
    try:
        return _abrir_whisper(hw.modelo, hw.device, hw.compute_type, hw.cpu_threads, cache_dir), hw
    except Exception:
        if not hw.usa_gpu:
            raise
        hw = hw.como_cpu(aviso="Não consegui usar a placa de vídeo; vou usar o processador (mais lento).")
        return _abrir_whisper(hw.modelo, "cpu", "int8", hw.cpu_threads, cache_dir), hw


def processar_trecho(modelo, audio: np.ndarray, t0: float,
                     detector: DetectorFundo | None = None) -> list[Linha]:
    """Transcreve um trecho (ex.: 30 s de gravação ao vivo) e devolve as linhas."""
    detector = detector if detector is not None else DetectorFundo()
    audio_n = normalizar_para_whisper(audio)
    if audio_n is None:
        return []
    segs, _ = modelo.transcribe(
        audio_n, language="pt", vad_filter=True, vad_parameters=dict(VAD),
        condition_on_previous_text=False, no_speech_threshold=0.8, log_prob_threshold=-1.5)
    linhas = []
    for s in segs:
        texto = s.text.strip()
        if not texto or eh_alucinacao(texto):
            continue
        a = int(s.start * TARGET_SR)
        b = max(int(s.end * TARGET_SR), a + 1)
        rms = float(np.sqrt(np.mean(audio[a:b] ** 2))) if a < len(audio) else 0.0
        linhas.append(Linha(t0 + s.start, texto, detector.e_fundo(rms)))
    return linhas


class ArquivoIlegivel(Exception):
    """O arquivo não é um áudio/vídeo que o programa consiga abrir."""


def transcrever_arquivo(modelo, caminho: str, idioma: str | None = "pt",
                        parar: threading.Event | None = None,
                        ao_progresso: Callable[[float, float], None] | None = None) -> Iterator[Linha]:
    """Transcreve um áudio/vídeo existente, gerando as linhas conforme ficam prontas.

    ``idioma=None`` detecta o idioma sozinho. ``ao_progresso(segundos_feitos, duracao)`` é
    chamado a cada trecho (e uma vez no começo, quando a duração já é conhecida).
    ``parar`` interrompe entre dois trechos; o que já saiu continua valendo.
    """
    from faster_whisper.audio import decode_audio
    try:
        audio = decode_audio(caminho, sampling_rate=TARGET_SR)
    except Exception as e:
        raise ArquivoIlegivel(str(e)) from e
    if len(audio) == 0:
        raise ArquivoIlegivel("áudio vazio")
    duracao = len(audio) / TARGET_SR
    if ao_progresso:
        ao_progresso(0.0, duracao)
    audio_n = normalizar_para_whisper(audio)       # só para o Whisper; o arquivo original não muda
    if audio_n is None or (parar and parar.is_set()):
        return
    segs, _ = modelo.transcribe(
        audio_n, language=idioma, vad_filter=True, vad_parameters=dict(VAD),
        condition_on_previous_text=False)
    for s in segs:
        if parar and parar.is_set():
            return
        if ao_progresso:
            ao_progresso(min(s.end, duracao), duracao)
        texto = s.text.strip()
        if texto and not eh_alucinacao(texto):
            yield Linha(s.start, texto)
    if ao_progresso:
        ao_progresso(duracao, duracao)
