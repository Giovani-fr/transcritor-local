import pytest

from transcritor.youtube import LinkInvalido, extrair_id, formatar_com_horarios, texto_corrido


@pytest.mark.parametrize("url", ["https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=10s",
                                 "https://youtu.be/dQw4w9WgXcQ", "https://www.youtube.com/shorts/dQw4w9WgXcQ",
                                 "dQw4w9WgXcQ"])
def test_extrair_id(url):
    assert extrair_id(url) == "dQw4w9WgXcQ"


def test_link_invalido():
    with pytest.raises(LinkInvalido):
        extrair_id("isso não é um link")


def test_formatos_de_texto():
    segs = [(0, "Olá, tudo bem."), (3725, "Segunda frase."), (4000, "Terceira.")]
    assert formatar_com_horarios(segs).splitlines()[1] == "[01:02:05] Segunda frase."
    assert texto_corrido(segs, frases_por_paragrafo=2).split("\n\n") == [
        "Olá, tudo bem. Segunda frase.", "Terceira."]
