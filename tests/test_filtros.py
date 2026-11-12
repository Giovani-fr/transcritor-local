import pytest

from transcritor.filtros import DetectorFundo, eh_alucinacao


@pytest.mark.parametrize("texto", ["Obrigado.", "obrigado", "Obrigado!", "Tchau.", "Muito obrigado.",
                                   "Legendas pela comunidade Amara.org", "...", "... ... ...",
                                   "  ", "", "…", "- .", "Legendas por Paulo Montenegro",
                                   "Tradução e legendas pela comunidade",
                                   "Thank you.", "Thanks for watching!", "Thank you for watching.",
                                   "Subtitles by Amara.org", "Subtitles by John Doe", "Captions by team",
                                   "Gracias.", "Muchas gracias.", "Subtítulos por la comunidad"])
def test_descarta_alucinacoes_e_pontuacao(texto):
    assert eh_alucinacao(texto)


@pytest.mark.parametrize("texto", ["Clica em renomear.", "Obrigado, então você seleciona o material.",
                                   "Tá selecionado já.", "Tchau, pessoal, até amanhã.",
                                   "Legendas de filmes ajudam muito a aprender idiomas",
                                   "As legendas por padrão ficam desligadas, então você precisa ativar no menu de configurações do player.",
                                   "Thank you for coming to today's lecture on machine learning.",
                                   "The subtitles by default are disabled in this video player so you need to enable them in settings."])
def test_mantem_fala_real(texto):
    assert not eh_alucinacao(texto)


def test_detector_fundo_nao_marca_antes_do_minimo():
    d = DetectorFundo()
    assert [d.e_fundo(0.1) for _ in range(3)] == [False] * 3
    assert d.e_fundo(0.001) is False        # só a 4ª amostra: ainda abaixo do mínimo de 5


def test_detector_fundo_marca_fala_baixa():
    d = DetectorFundo()
    for _ in range(10):
        d.e_fundo(0.1)
    assert d.e_fundo(0.01) is True          # bem abaixo do volume típico
    assert d.e_fundo(0.09) is False         # volume normal


def test_detector_fundo_janela_movel():
    d = DetectorFundo(janela=10)
    for _ in range(50):
        d.e_fundo(0.1)
    assert len(d.niveis) == 10
