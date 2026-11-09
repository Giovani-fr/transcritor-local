"""Detecção de hardware (GPU Nvidia/CUDA ou CPU) e escolha do modelo."""
from __future__ import annotations

import ctypes
import os
import site
import subprocess
from dataclasses import dataclass

MODELO_GPU = "large-v3-turbo"
MODELO_CPU = "small"
MODELO_CPU_FRACO = "base"
RAM_MINIMA_SMALL_GB = 6.0

AVISO_CPU = ("Não encontrei uma placa de vídeo Nvidia compatível, então vou usar o processador. "
             "Funciona normalmente, só é mais lento e um pouco menos preciso: um arquivo pode "
             "levar alguns minutos para ficar pronto e a gravação ao vivo pode atrasar um pouco.")

AVISO_CPU_CURTO = "Sem placa de vídeo Nvidia: vou usar o processador (mais lento)."

_dll_ok = False


def preparar_cuda() -> None:
    """Registra as DLLs CUDA instaladas via pip (nvidia-cublas/cudnn).

    Precisa rodar antes de importar ``faster_whisper``/``ctranslate2``.
    """
    global _dll_ok
    if _dll_ok or not hasattr(os, "add_dll_directory"):
        return
    bases = set(site.getsitepackages())
    try:
        bases.add(site.getusersitepackages())
    except Exception:
        pass
    for base in bases:
        for sub in ("cublas", "cudnn"):
            d = os.path.join(base, "nvidia", sub, "bin")
            if os.path.isdir(d):
                os.add_dll_directory(d)
                os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
    _dll_ok = True


def ram_total_gb() -> float:
    """RAM total em GB (Windows, sem dependências extras). 0.0 se não souber."""
    class _Mem(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("sullAvailExtendedVirtual", ctypes.c_ulonglong)]
    try:
        m = _Mem()
        m.dwLength = ctypes.sizeof(_Mem)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
        return m.ullTotalPhys / 1024 ** 3
    except Exception:
        return 0.0


def _cuda_disponivel() -> bool:
    try:
        preparar_cuda()
        import ctranslate2
        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


def _nome_gpu() -> str:
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total",
                            "--format=csv,noheader"], capture_output=True, text=True, timeout=5)
        return r.stdout.splitlines()[0].strip() if r.returncode == 0 and r.stdout.strip() else ""
    except Exception:
        return ""


@dataclass(frozen=True)
class Hardware:
    usa_gpu: bool
    modelo: str
    device: str
    compute_type: str
    gpu: str = ""
    ram_gb: float = 0.0
    aviso: str = ""
    cpu_threads: int = 0      # 0 = deixar o faster-whisper escolher

    def como_cpu(self, modelo: str = MODELO_CPU, aviso: str = AVISO_CPU) -> "Hardware":
        return Hardware(False, modelo, "cpu", "int8", self.gpu, self.ram_gb, aviso, self.cpu_threads)


def detectar_hardware(forcar_cpu: bool = False, modelo: str | None = None,
                      cpu_threads: int = 0) -> Hardware:
    """Escolhe dispositivo e modelo: GPU Nvidia → large-v3-turbo; senão CPU → small/base.

    ``modelo`` e ``cpu_threads`` permitem forçar a escolha (testes e medições).
    """
    ram = ram_total_gb()
    if not forcar_cpu and _cuda_disponivel():
        return Hardware(True, modelo or MODELO_GPU, "cuda", "int8_float16", _nome_gpu(), ram,
                        cpu_threads=cpu_threads)
    padrao = MODELO_CPU_FRACO if 0 < ram < RAM_MINIMA_SMALL_GB else MODELO_CPU
    return Hardware(False, modelo or padrao, "cpu", "int8", "", ram, AVISO_CPU, cpu_threads)
