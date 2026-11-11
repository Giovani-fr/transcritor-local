"""Dispositivos falsos para o processo filho de captura (precisam ser importáveis pelo processo novo)."""
import ctypes
import time

from transcritor.captura import ConjuntoCaptura, DispositivosSimulados, FonteSimulada


class FonteQueCai(FonteSimulada):
    """Depois de ~1,5 s de som, faz o que o PortAudio fez no PC real: acesso inválido à memória."""

    def coletar(self):
        x = super().coletar()
        if self._pos > 16000 * 1.5:
            ctypes.string_at(0)
        return x


class DispositivosQueCaem(DispositivosSimulados):
    def abrir(self, fonte, microfone=None):
        return ConjuntoCaptura([FonteQueCai("mic")])


class DispositivosQueTravam(DispositivosSimulados):
    def microfones(self):
        time.sleep(60)
