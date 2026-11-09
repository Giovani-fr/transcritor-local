"""Integração com o Whisper real (CPU, modelo `small`) usando só áudio sintético."""
import numpy as np
import pytest

from transcritor import motor

pytestmark = pytest.mark.modelo


def _ruido(rng, rms, seg=30):
    return (rng.standard_normal(seg * 16000) * rms).astype(np.float32)


def _grave(rng):
    x = np.convolve(rng.standard_normal(30 * 16000), np.ones(40) / 40, "same")
    return (x * 0.1).astype(np.float32)


def _tom():
    return (np.sin(2 * np.pi * 440 * np.arange(30 * 16000) / 16000) * 0.3).astype(np.float32)


@pytest.mark.parametrize("nome,fabrica", [
    ("silêncio digital", lambda rng: np.zeros(30 * 16000, np.float32)),
    ("ruído branco rms 0.015", lambda rng: _ruido(rng, 0.015)),
    ("ruído branco rms 0.003", lambda rng: _ruido(rng, 0.003)),
    ("ruído grave", _grave),
    ("tom puro 440 Hz", lambda rng: _tom()),
])
def test_sem_fala_nao_gera_texto(modelo_cpu, rng, nome, fabrica):
    assert motor.processar_trecho(modelo_cpu, fabrica(rng), 0.0) == [], nome


def test_fala_sintetica_gera_texto(modelo_cpu, fala):
    audio, _ = fala
    linhas = motor.processar_trecho(modelo_cpu, audio, 10.0)
    texto = " ".join(l.texto for l in linhas).lower()
    assert "cliente" in texto or "cadastr" in texto, texto
    assert linhas[0].inicio >= 10.0


def test_fala_no_meio_de_ruido(modelo_cpu, fala, rng):
    audio, _ = fala
    seg = np.concatenate([_ruido(rng, 0.005, 10), audio, _ruido(rng, 0.005, 10)])
    linhas = motor.processar_trecho(modelo_cpu, seg, 0.0)
    assert linhas and linhas[0].inicio >= 9.0       # a fala só começa depois dos 10 s de ruído


def test_fala_baixa_e_transcrita_gracas_a_normalizacao(modelo_cpu, fala):
    audio, _ = fala
    baixo = audio / np.abs(audio).max() * 0.05
    assert motor.processar_trecho(modelo_cpu, baixo, 0.0)


def test_transcrever_arquivo(modelo_cpu, fala):
    _, caminho = fala
    texto = " ".join(str(l) for l in motor.transcrever_arquivo(modelo_cpu, str(caminho))).lower()
    assert "cliente" in texto or "cadastr" in texto, texto
