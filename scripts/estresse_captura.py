"""Teste de estresse da captura: chama /dispositivos e /previa de forma repetida e concorrente.

Uso (com o servidor já no ar):  python -I -X utf8 scripts/estresse_captura.py [porta] [segundos]

A prévia só MEDE o volume e descarta o som na hora; nada é gravado. Mesmo assim, ela abre o
microfone de verdade (a luz de uso do Windows acende). Relata se o servidor continuou respondendo.
"""
from __future__ import annotations

import json
import sys
import threading
import time
import urllib.error
import urllib.request

PORTA = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
DURACAO = float(sys.argv[2]) if len(sys.argv) > 2 else 60
BASE = f"http://127.0.0.1:{PORTA}"
contagem = {"ok": 0, "erro_http": 0, "sem_servidor": 0}
primeira_falha: list[str] = []
trava = threading.Lock()
fim = time.time() + DURACAO


def chamar(metodo: str, caminho: str, corpo: dict | None = None) -> str:
    dados = json.dumps(corpo).encode() if corpo is not None else None
    req = urllib.request.Request(BASE + caminho, data=dados, method=metodo,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            r.read()
        return "ok"
    except urllib.error.HTTPError:
        return "erro_http"
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as e:
        with trava:
            if not primeira_falha:
                primeira_falha.append(f"{metodo} {caminho} -> {e!r} às {time.strftime('%H:%M:%S')}")
        return "sem_servidor"


def laco(metodo: str, caminho: str, corpos: list[dict | None], pausa: float) -> None:
    i = 0
    while time.time() < fim:
        r = chamar(metodo, caminho, corpos[i % len(corpos)])
        with trava:
            contagem[r] += 1
        i += 1
        time.sleep(pausa)


def main() -> int:
    tarefas = []
    for _ in range(4):        # a página chama /dispositivos várias vezes
        tarefas.append(threading.Thread(target=laco, args=("GET", "/api/gravacao/dispositivos", [None], 0.02)))
    for fonte in ("ambiente", "reuniao", "ambiente", "reuniao"):   # trocar a fonte recria a captura toda hora
        tarefas.append(threading.Thread(target=laco, args=("POST", "/api/gravacao/previa",
                                                           [{"fonte": fonte, "microfone": ""}], 0.05)))
    tarefas.append(threading.Thread(target=laco, args=("POST", "/api/gravacao/previa/parar", [None], 0.3)))
    for t in tarefas:
        t.start()
    for t in tarefas:
        t.join()
    vivo = chamar("GET", "/api/estado") == "ok"
    print(f"chamadas: {contagem}")
    print("servidor respondendo no fim:", "SIM" if vivo else "NÃO")
    if primeira_falha:
        print("primeira falha:", primeira_falha[0])
    return 0 if vivo and not contagem["sem_servidor"] else 1


if __name__ == "__main__":
    sys.exit(main())
