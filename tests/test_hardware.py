import pytest

from transcritor import hardware, motor
from transcritor.hardware import Hardware, detectar_hardware


def test_forcar_cpu_usa_small_e_avisa(monkeypatch):
    monkeypatch.setattr(hardware, "ram_total_gb", lambda: 16.0)
    hw = detectar_hardware(forcar_cpu=True)
    assert (hw.usa_gpu, hw.device, hw.modelo, hw.compute_type) == (False, "cpu", "small", "int8")
    assert "processador" in hw.aviso


def test_pouca_ram_usa_base(monkeypatch):
    monkeypatch.setattr(hardware, "ram_total_gb", lambda: 4.0)
    assert detectar_hardware(forcar_cpu=True).modelo == "base"


def test_sem_cuda_cai_para_cpu(monkeypatch):
    monkeypatch.setattr(hardware, "_cuda_disponivel", lambda: False)
    monkeypatch.setattr(hardware, "ram_total_gb", lambda: 8.0)
    assert not detectar_hardware().usa_gpu


def test_com_cuda_usa_turbo_sem_aviso(monkeypatch):
    monkeypatch.setattr(hardware, "_cuda_disponivel", lambda: True)
    monkeypatch.setattr(hardware, "_nome_gpu", lambda: "GPU de teste, 4096 MiB")
    hw = detectar_hardware()
    assert (hw.usa_gpu, hw.modelo, hw.device, hw.compute_type) == (True, "large-v3-turbo", "cuda", "int8_float16")
    assert hw.aviso == ""


def test_ram_total_e_positiva_no_windows():
    assert hardware.ram_total_gb() > 0


def test_falha_na_gpu_cai_para_cpu(monkeypatch):
    import faster_whisper
    usados = []

    def falso(modelo, device, compute_type, **kw):
        usados.append((modelo, device))
        if device == "cuda":
            raise RuntimeError("cublas64_12.dll not found")
        return "modelo-cpu"

    monkeypatch.setattr(faster_whisper, "WhisperModel", falso)
    gpu = Hardware(True, "large-v3-turbo", "cuda", "int8_float16")
    modelo, hw = motor.carregar_modelo(gpu)
    assert modelo == "modelo-cpu" and not hw.usa_gpu and hw.aviso
    assert usados == [("large-v3-turbo", "cuda"), ("small", "cpu")]


def test_falha_na_cpu_propaga(monkeypatch):
    import faster_whisper

    def falso(*a, **k):
        raise RuntimeError("sem memória")

    monkeypatch.setattr(faster_whisper, "WhisperModel", falso)
    with pytest.raises(RuntimeError):
        motor.carregar_modelo(Hardware(False, "small", "cpu", "int8"))
