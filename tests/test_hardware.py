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


# ---------- placa Nvidia sem CUDA utilizável x sem placa x escolha do usuário ----------
def _hw(monkeypatch, placa, cuda):
    monkeypatch.setattr(hardware, "ram_total_gb", lambda: 16.0)
    monkeypatch.setattr(hardware, "_nome_gpu", lambda: placa)
    monkeypatch.setattr(hardware, "_cuda_disponivel", lambda: cuda)


def test_placa_achada_mas_sem_cuda_pede_as_bibliotecas(monkeypatch):
    _hw(monkeypatch, "NVIDIA GeForce RTX 3050 Laptop GPU, 4096 MiB", cuda=False)
    hw = detectar_hardware()
    assert (hw.usa_gpu, hw.device, hw.modelo, hw.placa_sem_cuda) == (False, "cpu", "small", True)
    assert hw.aviso == ("Encontrei sua placa Nvidia, mas faltam as bibliotecas para usá-la. "
                        "Rode: pip install -r requirements-gpu.txt")
    assert "Não encontrei" not in hw.aviso


def test_sem_placa_nenhuma_diz_que_nao_encontrou(monkeypatch):
    _hw(monkeypatch, "", cuda=False)
    hw = detectar_hardware()
    assert not hw.usa_gpu and not hw.placa_sem_cuda and hw.aviso.startswith("Não encontrei uma placa de vídeo Nvidia")


def test_usar_processador_mesmo_com_placa(monkeypatch):
    _hw(monkeypatch, "GPU de teste, 4096 MiB", cuda=True)
    assert detectar_hardware().usa_gpu                                   # desligado por padrão
    hw = detectar_hardware(usar_processador=True)
    assert (hw.usa_gpu, hw.device, hw.modelo, hw.escolha_cpu, hw.placa_sem_cuda) == (False, "cpu", "small", True, False)
    assert "por escolha sua" in hw.aviso


def test_usar_processador_sem_placa_nao_muda_o_aviso(monkeypatch):
    _hw(monkeypatch, "", cuda=False)
    hw = detectar_hardware(usar_processador=True)
    assert not hw.escolha_cpu and hw.aviso.startswith("Não encontrei uma placa")
