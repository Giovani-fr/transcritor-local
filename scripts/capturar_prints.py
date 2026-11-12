"""Gera de novo TODOS os prints de docs/prints, com checagem de privacidade.

    pip install -r requirements-dev.txt   (Playwright) e   playwright install chromium
    python -I -X utf8 scripts/capturar_prints.py            (leva uns 4 minutos)

Nada aqui usa microfone, internet, modelo Whisper, placa de vídeo, vídeo de terceiros ou dados pessoais:
- o Whisper é um modelo falso que devolve frases inventadas;
- a captura de áudio é SIMULADA (tons gerados, nenhum dispositivo é aberto);
- o YouTube é um provedor simulado ("Aula de exemplo");
- o hardware (com e sem placa de vídeo) é descrito por objetos de teste.
Antes de salvar cada print, o texto visível da página é conferido (usuário do Windows, pastas, e-mail...).
Os prints vão primeiro para uma pasta temporária; só se TUDO der certo eles substituem os antigos em docs/prints
(assim um print velho nunca fica misturado com os novos e uma falha não deixa a pasta pela metade).
Detalhe: no print "gravando-atraso" o limite de 90 s do aviso de atraso é baixado para 20 s, só para não esperar 90 s.
"""
from __future__ import annotations

import argparse
import getpass
import shutil
import socket
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path

import numpy as np

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))

import uvicorn  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

import transcritor.servico as sv_mod  # noqa: E402
from transcritor import youtube as ytmod  # noqa: E402
from transcritor.captura import MSG_NAO_WINDOWS, DispositivosSimulados  # noqa: E402
from transcritor.hardware import AVISO_CPU, AVISO_SEM_BIBLIOTECAS, MODELO_GPU, Hardware  # noqa: E402
from transcritor.servico import Servico  # noqa: E402
from transcritor.web.app import criar_app  # noqa: E402

GPU = Hardware(True, MODELO_GPU, "cuda", "int8_float16", "NVIDIA GeForce RTX 3050 Laptop GPU", 15.7)
CPU = Hardware(False, "small", "cpu", "int8", ram_gb=15.7, aviso=AVISO_CPU)
SEM_BIBLIOTECAS = Hardware(False, "small", "cpu", "int8", "NVIDIA GeForce RTX 3050 Laptop GPU", 15.7,
                           AVISO_SEM_BIBLIOTECAS, placa_sem_cuda=True)

FRASES = [
    ("Bom dia, pessoal. Vamos começar a reunião de hoje.", "Primeiro, um resumo do que ficou combinado na semana passada."),
    ("O relatório está quase pronto, falta só revisar os números.", "Alguém tem alguma dúvida até aqui?"),
    ("Então fechamos assim: entrega na sexta-feira.", "Obrigada a todos pela participação."),
    ("Na próxima etapa vamos testar a nova versão com a equipe.", "Se algo der errado, avisem no grupo."),
]
POOL = [f for par in FRASES for f in par]


# ----------------------------------------------------------------------------- dados simulados
class Seg:
    def __init__(self, a, b, t):
        self.start, self.end, self.text = a, b, t


class ModeloDemo:
    """Whisper falso. Trecho curto (gravação ao vivo): duas frases. Arquivo longo: uma frase a cada 6 s."""

    def __init__(self):
        self.n = 0
        self.travar = False
        self.liberar = threading.Event()

    def transcribe(self, audio, **kw):
        if self.travar:
            self.liberar.wait(60)
        dur = len(audio) / 16000
        if dur <= 31:
            a, b = FRASES[self.n % len(FRASES)]
            self.n += 1
            return iter([Seg(2, 7, a), Seg(14, 21, b)]), None

        def gerar():
            for i in range(int(dur // 6)):
                time.sleep(0.3)
                yield Seg(i * 6, i * 6 + 5, POOL[i % len(POOL)])
        return gerar(), None


class YTDemo:
    """YouTube simulado. O comportamento depende do id do vídeo (11 caracteres)."""

    def info(self, url):
        vid = ytmod.extrair_id(url)
        erros = {"privado_xyz": "ERROR: [youtube] x: Private video. Sign in if you've been granted access",
                 "idade_abcde": "ERROR: [youtube] x: Sign in to confirm your age. This video may be inappropriate",
                 "removido_01": "ERROR: [youtube] x: Video unavailable"}
        if vid in erros:
            raise ytmod.classificar_ytdlp(erros[vid])
        return {"id": vid, "url": ytmod.url_canonica(vid), "titulo": "Aula de exemplo (vídeo simulado)",
                "canal": "Canal de exemplo", "duracao_s": 2712}

    def listar_legendas(self, vid):
        if vid == "sem_legenda":
            return []
        if vid == "legenda_nul":
            raise ytmod.ErroYoutube("x", "bloqueado")
        return [{"idioma": "pt", "automatica": True}]

    def legendas(self, vid):
        if vid == "legenda_nul":
            raise ytmod.ErroYoutube(ytmod.MSG_SEM_LEGENDA, "sem_legenda")
        return [(i * 6.0, f) for i, f in enumerate(POOL)], "pt", True

    def baixar_audio(self, url, pasta, ao_progresso, parar=None):
        vid = ytmod.extrair_id(url)
        if vid == "baixa_falha":
            raise ytmod.classificar_ytdlp("ERROR: unable to download video data: HTTP Error 403: Forbidden")
        if vid == "baixa_lenta":                 # fica parado em 45% até o usuário apertar Parar
            ao_progresso(0.45, 95)
            while not (parar is not None and parar.is_set()):
                time.sleep(0.05)
            raise ytmod.ErroYoutube("Download interrompido.", "cancelado")
        for i in range(1, 9):
            time.sleep(0.25)
            ao_progresso(i / 8, (8 - i) * 2)
        return Path(wav_sintetico(Path(pasta) / "audio.wav", 90))


def wav_sintetico(caminho, segundos):
    t = np.arange(int(segundos * 16000)) / 16000
    x = (np.sin(2 * np.pi * 220 * t) * 0.3 * 32767).astype(np.int16)
    with wave.open(str(caminho), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(x.tobytes())
    return str(caminho)


def atualizar_simulado():
    time.sleep(1.2)
    return {"antes": "2026.10.26", "depois": "2026.11.08", "mudou": True, "reiniciar": False}


# ----------------------------------------------------------------------------- infraestrutura
def porta_livre():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def esperar(cond, timeout=15.0):
    fim = time.time() + timeout
    while time.time() < fim:
        if cond():
            return True
        time.sleep(0.02)
    raise AssertionError("tempo esgotado esperando a condição")


class Servidor:
    def __init__(self, tmp: Path, nome: str, hw: Hardware, **kw):
        self.modelo = ModeloDemo()
        self.instalado = [True]
        kw.setdefault("instalado", lambda n, c=None: self.instalado[0])
        kw.setdefault("carregar", lambda h, c=None: (self.modelo, h))
        self.sv = Servico(tmp / f"saidas-{nome}", hw, youtube=YTDemo(), dispositivos=DispositivosSimulados(),
                          atualizar_ytdlp=atualizar_simulado, versao_ytdlp=lambda: "2026.10.26", **kw)
        self.porta = porta_livre()
        self.uv = uvicorn.Server(uvicorn.Config(criar_app(self.sv, iniciar=False), host="127.0.0.1",
                                                port=self.porta, log_level="warning"))
        self.base = f"http://127.0.0.1:{self.porta}"

    def iniciar(self, esperar_pronto=True):
        self.sv.iniciar()
        threading.Thread(target=self.uv.run, daemon=True).start()
        esperar(lambda: self.uv.started)
        if esperar_pronto:
            esperar(lambda: self.sv.estado_modelo["fase"] == "pronto")
        return self

    def parar(self):
        self.uv.should_exit = True


class Prints:
    def __init__(self, saida: Path, proibidos: list[str]):
        self.saida, self.proibidos, self.feitos = saida, proibidos, []

    def checar(self, page, nome):
        texto = page.evaluate(
            "document.body.innerText + ' ' + [...document.querySelectorAll('[title],[href],[value],input,option')]"
            ".map(e => (e.getAttribute('title')||'') + (e.getAttribute('href')||'') + (e.value||'') + (e.textContent||'')).join(' ')")
        achados = [p for p in self.proibidos if p.lower() in texto.lower()]
        assert not achados, f"{nome}: dados pessoais visíveis no print: {achados}"

    def __call__(self, page, nome):
        self.checar(page, nome)
        page.screenshot(path=str(self.saida / f"{nome}.png"))
        self.feitos.append(nome)
        print("print:", nome, flush=True)


def pct(page, seletor, minimo, timeout=60000):
    page.wait_for_function(
        f"parseInt(document.querySelector('{seletor}').getAttribute('aria-valuenow')||'0') >= {minimo}",
        timeout=timeout, polling=100)


def abrir(page, base, aba=None):
    page.goto(base)
    page.wait_for_selector("#inicio-arquivo:not([hidden])", timeout=30000)
    if aba:
        page.click(f'.aba[data-aba="{aba}"]')
        page.wait_for_selector(f"#inicio-{aba}:not([hidden])")


def nivel(page, seletor, minimo=35):
    page.wait_for_function(
        f"[...document.querySelectorAll('{seletor} .med-nivel')].some(e => parseFloat(e.style.width) >= {minimo})", timeout=15000)


def buscar(page, vid):
    page.fill("#yt-url", f"https://www.youtube.com/watch?v={vid}")
    page.click("#btn-yt-buscar")


# ----------------------------------------------------------------------------- roteiros
def primeiro_uso(shot, pg, srv, tmp):
    srv.instalado[0] = False
    liberar = threading.Event()

    def baixar(nome, prog, cache):
        prog(0.2, 20, 100)
        prog(0.45, 45, 100)
        liberar.wait(30)
        prog(1.0, 100, 100)
        srv.instalado[0] = True

    srv.sv._baixar = baixar
    srv.iniciar(esperar_pronto=False)
    pg.goto(srv.base)
    pg.wait_for_selector("#modelo-faltando:not([hidden])")
    shot(pg, "01-primeiro-uso")
    pg.click("#btn-baixar")
    pg.wait_for_selector("#modelo-baixando:not([hidden])")
    pct(pg, "#barra-modelo", 45)
    shot(pg, "02-baixando-modelo")
    liberar.set()
    pg.wait_for_selector("#painel:not([hidden])", timeout=30000)
    pg.wait_for_selector("#inicio-arquivo:not([hidden])")
    shot(pg, "03-arquivo-inicio")
    pg.click("#inicio-arquivo summary")
    shot(pg, "04-avancado")
    pg.click("#inicio-arquivo summary")


def aba_arquivo(shot, pg, srv, audio, ruim):
    # parar no meio
    pg.set_input_files("#entrada-arquivo", audio)
    pg.wait_for_selector("#arq-andamento:not([hidden])")
    pct(pg, "#barra-job", 15)
    pg.click("#btn-parar")
    pg.wait_for_selector("#arq-fim:not([hidden])", timeout=60000)
    assert "Parado" in pg.inner_text("#fim-selo"), pg.inner_text("#fim-selo")
    shot(pg, "05-parado")
    pg.click("#btn-outro")
    # transcrição completa
    pg.set_input_files("#entrada-arquivo", audio)
    pg.wait_for_selector("#arq-andamento:not([hidden])")
    pg.wait_for_function("document.querySelectorAll('#texto-vivo .l').length >= 4", polling=100, timeout=60000)
    pct(pg, "#barra-job", 40)
    shot(pg, "06-transcrevendo")
    pg.wait_for_selector("#arq-fim:not([hidden])", timeout=60000)
    pg.wait_for_function("document.querySelectorAll('#texto-final .l').length > 3")
    shot(pg, "07-resultado")
    if pg.is_checked("#chk-horarios"):
        pg.uncheck("#chk-horarios", force=True)
    pg.wait_for_function("document.querySelectorAll('#texto-final p').length >= 1")
    shot(pg, "08-texto-corrido")
    pg.check("#chk-horarios", force=True)
    pg.click("#btn-tema")
    pg.wait_for_function("document.documentElement.dataset.tema === 'escuro'")
    shot(pg, "09-tema-escuro")
    pg.click("#btn-tema")
    # erro: arquivo que não é áudio
    pg.click("#btn-outro")
    pg.set_input_files("#entrada-arquivo", ruim)
    pg.wait_for_selector("#arq-fim:not([hidden])", timeout=60000)
    shot(pg, "10-erro")
    pg.click("#btn-outro")
    pg.wait_for_selector("#lista-historico li")
    shot(pg, "11-historico")


def aba_youtube(shot, pg, srv):
    pg.click('.aba[data-aba="youtube"]')
    pg.wait_for_selector("#inicio-youtube:not([hidden])")
    shot(pg, "16-youtube-inicio")
    pg.fill("#yt-url", "https://exemplo.com/qualquer-coisa")
    pg.click("#btn-yt-buscar")
    pg.wait_for_selector("#yt-erro:not([hidden])")
    shot(pg, "17-youtube-link-invalido")
    buscar(pg, "video_ok_01")
    pg.wait_for_selector("#yt-video:not([hidden])")
    shot(pg, "18-youtube-video")
    # Rápido
    pg.click("#btn-yt-transcrever")
    pg.wait_for_selector("#arq-fim:not([hidden])", timeout=30000)
    pg.wait_for_function("document.querySelectorAll('#texto-final .l').length > 3")
    shot(pg, "19-youtube-rapido-resultado")
    pg.uncheck("#chk-horarios", force=True)
    pg.wait_for_function("document.querySelectorAll('#texto-final p').length >= 1")
    shot(pg, "20-youtube-texto-corrido")
    pg.check("#chk-horarios", force=True)
    # Preciso
    pg.click("#btn-outro")
    pg.wait_for_selector("#inicio-youtube:not([hidden])")
    buscar(pg, "video_ok_01")
    pg.wait_for_selector("#yt-video:not([hidden])")
    pg.check('input[name="yt-modo"][value="preciso"]', force=True)
    pg.click("#inicio-youtube summary")
    pg.select_option("#yt-idioma", "en")
    shot(pg, "21-youtube-preciso-escolhido")
    pg.click("#btn-yt-transcrever")
    vistos, t0 = [], time.time()
    while time.time() - t0 < 60:
        if not pg.locator("#arq-andamento").is_hidden():
            st = pg.inner_text("#and-status")
            if st not in vistos:
                vistos.append(st)
                if "Baixando" in st and not any("Baixando" in v for v in vistos[:-1]):
                    shot(pg, "22-youtube-baixando-audio")
                if "Transcrevendo" in st and not any("Transcrevendo" in v for v in vistos[:-1]):
                    pg.wait_for_function("document.querySelectorAll('#texto-vivo .l').length >= 2", timeout=30000)
                    shot(pg, "23-youtube-preciso-transcrevendo")
        elif not pg.locator("#arq-fim").is_hidden():
            break
        pg.wait_for_timeout(40)
    assert {"22-youtube-baixando-audio", "23-youtube-preciso-transcrevendo"} <= set(shot.feitos), vistos
    pg.wait_for_function("document.querySelectorAll('#texto-final .l').length > 3")
    shot(pg, "24-youtube-preciso-resultado")
    pg.click("#btn-outro")
    pg.wait_for_selector("#lista-historico li")
    shot(pg, "25-youtube-historico")
    # sem legenda: oferece o modo Preciso
    buscar(pg, "sem_legenda")
    pg.wait_for_selector("#yt-sem-legenda:not([hidden])")
    assert pg.is_disabled("#btn-yt-transcrever")
    shot(pg, "28-youtube-sem-legenda")
    pg.click("#btn-yt-usar-preciso")
    # privado / idade
    for vid, nome in [("privado_xyz", "29-youtube-video-privado"), ("idade_abcde", "30-youtube-restricao-idade")]:
        buscar(pg, vid)
        pg.wait_for_selector("#yt-erro:not([hidden])")
        shot(pg, nome)
    # download parado em 45% e botão Parar
    buscar(pg, "baixa_lenta")
    pg.wait_for_selector("#yt-video:not([hidden])")
    pg.check('input[name="yt-modo"][value="preciso"]', force=True)
    pg.click("#btn-yt-transcrever")
    pg.wait_for_function("document.querySelector('#and-status') && document.querySelector('#and-status').textContent.includes('45%')", timeout=20000)
    pg.wait_for_timeout(700)
    shot(pg, "31-youtube-baixando-45")
    pg.click("#btn-parar")
    pg.wait_for_selector("#arq-fim:not([hidden])")
    pg.click("#btn-outro")
    # falha de download → atualizar o yt-dlp
    buscar(pg, "baixa_falha")
    pg.wait_for_selector("#yt-video:not([hidden])")
    pg.check('input[name="yt-modo"][value="preciso"]', force=True)
    pg.click("#btn-yt-transcrever")
    pg.wait_for_selector("#arq-fim:not([hidden])")
    pg.wait_for_selector("#fim-ajuda .ajuda")
    shot(pg, "32-youtube-falha-download")
    pg.click("#btn-outro")
    # Rápido sem legenda descoberta no meio → saída para o Preciso
    buscar(pg, "legenda_nul")
    pg.wait_for_selector("#yt-video:not([hidden])")
    pg.click("#btn-yt-transcrever")
    pg.wait_for_selector("#arq-fim:not([hidden])")
    pg.wait_for_selector("#fim-acao-preciso:not([hidden])")
    shot(pg, "33-youtube-rapido-sem-legenda")
    pg.click("#btn-fim-preciso")
    pg.wait_for_selector("#arq-andamento:not([hidden])")
    pg.click("#btn-parar")
    pg.wait_for_selector("#arq-fim:not([hidden])")
    pg.click("#btn-outro")


def aba_gravar(shot, pg, srv):
    sv, modelo, base = srv.sv, srv.modelo, srv.base
    abrir(pg, base, "gravar")
    nivel(pg, "#med-previa")
    shot(pg, "36-gravar-inicio")
    pg.click("#inicio-gravar details.avancado summary")
    pg.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    shot(pg, "37-gravar-avancado")
    pg.click("#inicio-gravar details.avancado summary")
    pg.evaluate("window.scrollTo(0, 0)")
    pg.click("#btn-gravar-iniciar")
    pg.wait_for_selector("#grav-aviso-reuniao:not([hidden])")
    shot(pg, "38-gravar-aviso-reuniao")
    pg.click("#btn-grav-voltar")
    # som baixo demais
    sv.dispositivos = DispositivosSimulados(amp_mic=0.004)
    pg.check('input[name="grav-fonte"][value="ambiente"]', force=True)
    abrir(pg, base, "gravar")
    pg.wait_for_function("document.querySelector('#med-previa .med-aviso:not([hidden])')", timeout=15000)
    pg.wait_for_timeout(500)
    shot(pg, "39-gravar-som-baixo")
    # sem microfone
    sv.dispositivos = DispositivosSimulados(sem_microfone=True)
    abrir(pg, base, "gravar")
    pg.wait_for_selector("#grav-erro:not([hidden])", timeout=10000)
    pg.click("#btn-gravar-iniciar")
    pg.wait_for_timeout(500)
    shot(pg, "40-gravar-sem-microfone")

    # outro sistema (sem WASAPI)
    class NaoWindows(DispositivosSimulados):
        def suportado(self):
            return False, MSG_NAO_WINDOWS
    sv.dispositivos = NaoWindows()
    abrir(pg, base, "gravar")
    pg.wait_for_selector("#grav-indisponivel:not([hidden])", timeout=10000)
    shot(pg, "41-gravar-indisponivel")
    # gravando (simulado), modo Reunião
    sv.dispositivos = DispositivosSimulados()
    abrir(pg, base, "gravar")
    pg.check('input[name="grav-fonte"][value="reuniao"]', force=True)
    nivel(pg, "#med-previa", 5)
    pg.click("#btn-gravar-iniciar")
    pg.click("#btn-grav-confirmar")
    pg.wait_for_selector("#grav-extra:not([hidden])")
    pg.wait_for_function("document.querySelector('#grav-tempo').textContent >= '00:00:05'", timeout=20000)
    shot(pg, "42-gravando-sem-texto")
    pg.wait_for_function("document.querySelectorAll('#texto-vivo .l').length >= 2", timeout=60000)
    pg.wait_for_timeout(1500)
    shot(pg, "43-gravando-texto-ao-vivo")
    pg.click('.aba[data-aba="arquivo"]')                 # a aba não troca no meio da gravação
    pg.click("#btn-parar")
    pg.wait_for_selector("#arq-fim:not([hidden])", timeout=30000)
    pg.wait_for_selector("#texto-final .l")
    pg.wait_for_timeout(2800)                            # deixa o aviso temporário sumir
    shot(pg, "44-gravacao-resultado")
    pg.click("#btn-outro")
    pg.wait_for_selector("#inicio-gravar:not([hidden])")
    # transcrição atrasada (limite de 90 s baixado para 20 s só para o print)
    original = sv_mod.ATRASO_AVISO_S
    sv_mod.ATRASO_AVISO_S = 20
    modelo.travar = True
    try:
        pg.click('.aba[data-aba="gravar"]')
        pg.check('input[name="grav-fonte"][value="ambiente"]', force=True)
        pg.wait_for_timeout(800)
        pg.click("#btn-gravar-iniciar")
        pg.wait_for_selector("#grav-extra:not([hidden])")
        pg.wait_for_selector("#grav-atraso:not([hidden])", timeout=60000)
        nivel(pg, "#med-grav")
        shot(pg, "45-gravando-atraso")
        pg.click("#btn-parar")
        pg.wait_for_function("document.querySelector('#grav-estado').textContent.includes('Terminando')", timeout=10000)
        pg.wait_for_timeout(500)
        shot(pg, "46-gravacao-terminando")
        modelo.liberar.set()
        pg.wait_for_selector("#arq-fim:not([hidden])", timeout=30000)
    finally:
        modelo.travar = False
        sv_mod.ATRASO_AVISO_S = original
    pg.click("#btn-outro")
    pg.wait_for_timeout(400)
    shot(pg, "47-gravar-historico")
    pg.emulate_media(color_scheme="dark")
    pg.wait_for_timeout(300)
    nivel(pg, "#med-previa")
    shot(pg, "48-gravar-escuro")
    pg.emulate_media(color_scheme="light")


def celular(shot, nav, srv, aba_cheia):
    ctx = nav.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, locale="pt-BR",
                          color_scheme="light", is_mobile=True, has_touch=True)
    m = ctx.new_page()
    abrir(m, srv.base)
    shot(m, "12-celular-inicio")
    assert m.evaluate("document.documentElement.scrollWidth") == 390, "rolagem horizontal no celular"
    m.locator("#lista-historico li button", has_text="reuniao-de-exemplo").first.click()
    m.wait_for_selector("#arq-fim:not([hidden])")
    m.wait_for_function("document.querySelectorAll('#texto-final .l').length > 3")
    m.evaluate("window.scrollTo(0,0)")
    shot(m, "13-celular-resultado")
    m.emulate_media(color_scheme="dark")
    m.wait_for_timeout(400)
    shot(m, "14-celular-escuro")
    m.emulate_media(color_scheme="light")
    # YouTube
    abrir(m, srv.base, "youtube")
    buscar(m, "video_ok_01")
    m.wait_for_selector("#yt-video:not([hidden])")
    m.evaluate("window.scrollTo(0,0)")
    shot(m, "34-celular-youtube-video")
    buscar(m, "baixa_falha")
    m.wait_for_selector("#yt-video:not([hidden])")
    m.check('input[name="yt-modo"][value="preciso"]', force=True)
    m.click("#btn-yt-transcrever")
    m.wait_for_selector("#fim-ajuda .ajuda")
    m.evaluate("window.scrollTo(0,0)")
    shot(m, "35-celular-youtube-falha-download")
    # Gravar
    abrir(m, srv.base, "gravar")
    nivel(m, "#med-previa")
    shot(m, "49-celular-gravar-inicio")
    m.click("#btn-gravar-iniciar")
    m.click("#btn-grav-confirmar")
    m.wait_for_selector("#grav-extra:not([hidden])")
    m.wait_for_function("document.querySelector('#grav-tempo').textContent >= '00:00:04'", timeout=20000)
    nivel(m, "#med-grav")
    shot(m, "50-celular-gravando")
    m.click("#btn-parar")
    m.wait_for_selector("#arq-fim:not([hidden])", timeout=30000)
    for tela in (m,):
        assert tela.evaluate("document.documentElement.scrollWidth") == 390
    ctx.close()


def atualizar_ytdlp(shot, pg, srv):
    abrir(pg, srv.base, "youtube")
    buscar(pg, "baixa_falha")
    pg.wait_for_selector("#yt-video:not([hidden])")
    pg.check('input[name="yt-modo"][value="preciso"]', force=True)
    pg.click("#btn-yt-transcrever")
    pg.wait_for_selector("#fim-ajuda .ajuda")
    shot(pg, "51-ytdlp-botao-atualizar")
    botao = pg.get_by_role("button", name="Atualizar yt-dlp agora", exact=True)
    botao.click()
    pg.wait_for_selector("#fim-ajuda .confirmar:not([hidden])")
    shot(pg, "52-ytdlp-confirmar")
    pg.get_by_role("button", name="Cancelar", exact=True).click()
    assert botao.is_visible()
    botao.click()
    pg.get_by_role("button", name="Continuar", exact=True).click()
    pg.wait_for_timeout(300)
    shot(pg, "53-ytdlp-atualizando")
    pg.wait_for_selector("#fim-ajuda .ajuda-resultado:not([hidden])", timeout=15000)
    pg.click("#fim-ajuda summary")
    shot(pg, "54-ytdlp-atualizado")


def sem_placa_de_video(shot, nav, srv, audio):
    """Mesmo programa sem placa Nvidia: aviso (aberto na 1ª vez, recolhido depois) e uma transcrição em CPU."""
    ctx = nav.new_context(viewport={"width": 1000, "height": 900}, locale="pt-BR", color_scheme="light")
    pg = ctx.new_page()
    abrir(pg, srv.base)
    shot(pg, "26-aviso-cpu-primeira-vez")
    pg.reload()
    pg.wait_for_selector("#inicio-arquivo:not([hidden])")
    assert not pg.is_visible("#hw-detalhe")
    shot(pg, "27-aviso-cpu-recolhido")
    pg.set_input_files("#entrada-arquivo", audio)
    pg.wait_for_selector("#arq-andamento:not([hidden])")
    pg.wait_for_function("document.querySelectorAll('#texto-vivo .l').length >= 4", polling=100, timeout=60000)
    pct(pg, "#barra-job", 40)
    shot(pg, "55-cpu-transcrevendo")
    pg.wait_for_selector("#arq-fim:not([hidden])", timeout=60000)
    pg.wait_for_function("document.querySelectorAll('#texto-final .l').length > 3")
    shot(pg, "56-cpu-resultado")
    ctx.close()


def placa_sem_bibliotecas(shot, nav, srv):
    """Há placa Nvidia, mas faltam as bibliotecas CUDA: aviso com o comando e o botão "Copiar comando"."""
    ctx = nav.new_context(viewport={"width": 1000, "height": 900}, locale="pt-BR", color_scheme="light")
    pg = ctx.new_page()
    abrir(pg, srv.base)
    assert pg.is_visible("#hw-copiar") and "requirements-gpu.txt" in pg.inner_text("#hw-msg")
    assert not pg.is_visible(".opcao-cpu")                  # sem CUDA utilizável, não há o que trocar
    shot(pg, "57-aviso-placa-sem-bibliotecas")
    ctx.close()


def avancado_usar_processador(shot, pg, srv):
    """Com placa utilizável, "Avançado" oferece usar o processador (desligado por padrão)."""
    abrir(pg, srv.base)
    pg.click("#inicio-arquivo summary")
    caixa = pg.locator("#inicio-arquivo .chk-cpu")
    assert caixa.is_visible() and not caixa.is_checked()
    shot(pg, "58-avancado-usar-processador")


# ----------------------------------------------------------------------------- principal
def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--saida", default=str(RAIZ / "docs" / "prints"), help="pasta final dos prints")
    destino = Path(ap.parse_args().saida)

    ytmod.ajuda_atualizar = lambda: {            # nos prints o comando não mostra o caminho do seu PC
        "tipo": "atualizar_ytdlp", "versao": "2026.10.26",
        "comando": r'"C:\...\venvs\transcritor-local\Scripts\python.exe" -m pip install --upgrade yt-dlp'}

    with tempfile.TemporaryDirectory(prefix="prints-") as t:
        tmp = Path(t)
        out = tmp / "prints"
        out.mkdir()
        entradas = tmp / "entradas"
        entradas.mkdir()
        audio = wav_sintetico(entradas / "reuniao-de-exemplo.wav", 90)
        ruim = entradas / "arquivo-invalido.mp3"
        ruim.write_bytes(b"isto nao e um audio")
        proibidos = [getpass.getuser(), "OneDrive", "AppData", "@gmail", "Documents", "C:\\Users",
                     str(Path.home()), str(tmp), "/Users/"]
        shot = Prints(out, [p for p in dict.fromkeys(proibidos) if p])

        gpu = Servidor(tmp, "gpu", GPU)
        cpu = Servidor(tmp, "cpu", CPU).iniciar()
        semlib = Servidor(tmp, "semlib", SEM_BIBLIOTECAS).iniciar()
        with sync_playwright() as p:
            try:
                nav = p.chromium.launch()
            except Exception:
                nav = p.chromium.launch(channel="chrome")
            ctx = nav.new_context(viewport={"width": 1000, "height": 900}, locale="pt-BR", color_scheme="light")
            pg = ctx.new_page()
            erros_js = []
            pg.on("pageerror", lambda e: erros_js.append(str(e)))
            primeiro_uso(shot, pg, gpu, tmp)
            aba_arquivo(shot, pg, gpu, audio, str(ruim))
            aba_youtube(shot, pg, gpu)
            aba_gravar(shot, pg, gpu)
            atualizar_ytdlp(shot, pg, gpu)
            avancado_usar_processador(shot, pg, gpu)
            celular(shot, nav, gpu, True)
            sem_placa_de_video(shot, nav, cpu, audio)
            placa_sem_bibliotecas(shot, nav, semlib)
            assert not erros_js, f"erros de JavaScript: {erros_js}"
            nav.close()
        gpu.parar()
        cpu.parar()
        semlib.parar()

        numeros = sorted(int(n[:2]) for n in shot.feitos)
        faltando = sorted(set(range(1, 59)) - set(numeros) - {15})
        assert not faltando, f"prints que não foram gerados: {faltando}"
        destino.mkdir(parents=True, exist_ok=True)
        for velho in destino.glob("*.png"):
            velho.unlink()
        for novo in out.glob("*.png"):
            shutil.copy2(novo, destino / novo.name)
        print(f"OK: {len(shot.feitos)} prints em {destino}")


if __name__ == "__main__":
    main()
