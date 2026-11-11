"""Detecção de hardware (GPU Nvidia/CUDA ou CPU) e escolha do modelo."""
from __future__ import annotations

import ctypes
import os
import site
import subprocess
import sys
from dataclasses import dataclass

MODELO_GPU = "large-v3-turbo"
MODELO_CPU = "small"
MODELO_CPU_FRACO = "base"
RAM_MINIMA_SMALL_GB = 6.0

AVISO_CPU = ("Não encontrei uma placa de vídeo Nvidia compatível, então vou usar o processador. "
             "Funciona normalmente, só é mais lento e um pouco menos preciso: um arquivo pode "
             "levar alguns minutos para ficar pronto e a gravação ao vivo pode atrasar um pouco.")

AVISO_CPU_CURTO = "Sem placa de vídeo Nvidia: vou usar o processador (mais lento)."

COMANDO_GPU = "pip install -r requirements-gpu.txt"
AVISO_SEM_BIBLIOTECAS = f"Encontrei sua placa Nvidia, mas faltam as bibliotecas para usá-la. Rode: {COMANDO_GPU}"
AVISO_SEM_BIBLIOTECAS_DETALHE = (
    "Depois de instalar, feche a janela preta e abra o iniciar.bat de novo. Se você já instalou e este aviso "
    "continua, o driver da placa Nvidia pode estar antigo: atualize-o. Enquanto isso o programa usa o "
    "processador, que funciona normalmente, só é mais lento e um pouco menos preciso.")
AVISO_CPU_ESCOLHA = "Usando o processador por escolha sua, mesmo com placa de vídeo (mais lento)."
AVISO_CPU_ESCOLHA_DETALHE = ('Para voltar a usar a placa de vídeo, desmarque "Usar o processador mesmo com placa de '
                             'vídeo" em Avançado.')

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


def _bibliotecas_cuda_ok() -> bool:
    """As DLLs que o faster-whisper precisa na placa (cuBLAS e cuDNN) carregam?

    O ctranslate2 enxerga a placa (``get_cuda_device_count``) só pelo driver, mesmo sem essas bibliotecas;
    aí o modelo falharia na hora de carregar. Por isso conferimos as DLLs de verdade.
    """
    if sys.platform != "win32":
        return True
    for dll in ("cublas64_12.dll", "cudnn64_9.dll"):
        try:
            ctypes.WinDLL(dll)
        except OSError:
            return False
    return True


def _cuda_disponivel() -> bool:
    try:
        preparar_cuda()
        import ctranslate2
        return ctranslate2.get_cuda_device_count() > 0 and _bibliotecas_cuda_ok()
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
    placa_sem_cuda: bool = False   # há placa Nvidia, mas o CUDA não está utilizável (bibliotecas ou driver)
    escolha_cpu: bool = False      # o usuário pediu o processador mesmo com placa de vídeo

    def como_cpu(self, modelo: str = MODELO_CPU, aviso: str = AVISO_CPU) -> "Hardware":
        return Hardware(False, modelo, "cpu", "int8", self.gpu, self.ram_gb, aviso, self.cpu_threads,
                        self.placa_sem_cuda, self.escolha_cpu)


def detectar_hardware(forcar_cpu: bool = False, modelo: str | None = None,
                      cpu_threads: int = 0, usar_processador: bool = False) -> Hardware:
    """Escolhe dispositivo e modelo: GPU Nvidia → large-v3-turbo; senão CPU → small/base.

    Casos de CPU, cada um com seu aviso:
    - ``forcar_cpu`` (testes/medições): como se não houvesse placa;
    - ``usar_processador`` (escolha do usuário em "Avançado"): vale mesmo com placa de vídeo;
    - placa Nvidia achada pelo ``nvidia-smi`` mas CUDA inutilizável: pede para instalar ``requirements-gpu.txt``;
    - nenhuma placa: "não encontrei placa Nvidia".
    ``modelo`` e ``cpu_threads`` permitem forçar a escolha (testes e medições).
    """
    ram = ram_total_gb()
    padrao = MODELO_CPU_FRACO if 0 < ram < RAM_MINIMA_SMALL_GB else MODELO_CPU
    if forcar_cpu:
        return Hardware(False, modelo or padrao, "cpu", "int8", "", ram, AVISO_CPU, cpu_threads)
    placa = _nome_gpu()
    cuda = _cuda_disponivel()
    if cuda and not usar_processador:
        return Hardware(True, modelo or MODELO_GPU, "cuda", "int8_float16", placa, ram, cpu_threads=cpu_threads)
    if usar_processador and (cuda or placa):
        return Hardware(False, modelo or padrao, "cpu", "int8", placa, ram, AVISO_CPU_ESCOLHA, cpu_threads,
                        escolha_cpu=True)
    if placa:
        return Hardware(False, modelo or padrao, "cpu", "int8", placa, ram, AVISO_SEM_BIBLIOTECAS, cpu_threads,
                        placa_sem_cuda=True)
    return Hardware(False, modelo or padrao, "cpu", "int8", "", ram, AVISO_CPU, cpu_threads)
