"""Testes com a internet de verdade. Fora da execução padrão: rode com  pytest -m rede

Usam um vídeo publicado pelo canal NASA Goddard ("The Future of Fires"). Material da NASA é, em geral,
de domínio público (confira as diretrizes de mídia da NASA e a página do vídeo). Nenhum conteúdo do
vídeo é salvo no repositório: o áudio vai para uma pasta temporária e é apagado.
"""
import pytest

from transcritor.youtube import ProvedorYoutube

pytestmark = pytest.mark.rede

URL = "https://www.youtube.com/watch?v=_lSO02pkQZU"
ID = "_lSO02pkQZU"


def test_info_do_video():
    info = ProvedorYoutube().info(URL)
    assert info["id"] == ID and "Future of Fires" in info["titulo"] and 60 < info["duracao_s"] < 300


def test_legendas():
    p = ProvedorYoutube()
    assert any(o["idioma"].startswith("en") for o in p.listar_legendas(ID))
    segs, idioma, automatica = p.legendas(ID)
    assert idioma.startswith("en") and len(segs) > 5 and all(isinstance(t, float) for t, _ in segs)


def test_baixa_so_o_audio(tmp_path):
    progresso = []
    arq = ProvedorYoutube().baixar_audio(URL, tmp_path, lambda f, eta: progresso.append(f))
    assert arq.exists() and arq.stat().st_size > 50_000 and arq.suffix in (".m4a", ".webm", ".mp4", ".opus")
    assert progresso and progresso[-1] == pytest.approx(1.0, abs=0.01)
    assert not list(tmp_path.glob("*.part"))
