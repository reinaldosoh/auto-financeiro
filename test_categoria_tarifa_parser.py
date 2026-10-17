"""Parser e resolução por nome — categorias tarifa."""
import unittest

from machine_categoria_http import (
    _CategoriaFormParser,
    _sincronizar_icone_para_post,
    listar_categorias_tarifa,
    resolver_categoria_por_nome,
)

LISTA_FIXTURE = """
<html><body>
<span class="target_number"><h3>1</h3></span>
<span class="material-icons">phone_iphone</span>
<a onclick="duplicarCategoria('10', 'Alpha')"></a>
<span class="target_number"><h3>2</h3></span>
<a onclick="duplicarCategoria('20', 'Maçaneta')"></a>
<span class="target_number"><h3>3</h3></span>
<a onclick="duplicarCategoria('30', 'Beta OCULTA')"></a>
</body></html>
"""

FORM_FIXTURE = """
<form id="personalizar-form" action="/categoria/update/20" method="post">
<input type="hidden" name="Categoria[exibir_app]" value="0" />
<input type="text" name="Categoria[nome]" value="Maçaneta" />
<select name="dropdown_icone_app">
  <option value="C" selected="selected">Comum</option>
</select>
<input type="text" name="Categoria[icone_app]" value="" />
<input type="text" name="Categoria[eh_icone_personalizado]" value="" />
<select name="Categoria[ordem_exibicao_app]">
  <option value="1">1</option>
  <option value="2" selected="selected">2</option>
</select>
</form>
<script>const iconeAtual = "isometrico_comum1_amarelo";</script>
"""


class CategoriaTarifaParserTests(unittest.TestCase):
    def test_listar_fixture(self):
        class FakeResp:
            status_code = 200
            url = "https://cloud.taximachine.com.br/categoria/tarifa"
            text = LISTA_FIXTURE

        class FakeHttp:
            def get(self, url, timeout=120):
                return FakeResp()

        cats = listar_categorias_tarifa(FakeHttp())
        self.assertEqual(len(cats), 3)
        self.assertEqual(cats[1]["nome"], "Maçaneta")
        self.assertEqual(cats[1]["ordem"], 2)
        self.assertEqual(cats[0]["categoria_id"], 10)

    def test_resolver_nome(self):
        cats = [
            {"categoria_id": 10, "nome": "Alpha", "ordem": 1},
            {"categoria_id": 20, "nome": "Maçaneta", "ordem": 2},
        ]
        r = resolver_categoria_por_nome("maçaneta", cats)
        self.assertEqual(r["categoria_id"], 20)

    def test_parse_form(self):
        fields = _CategoriaFormParser.parse(FORM_FIXTURE)
        self.assertEqual(fields["Categoria[nome]"], ["Maçaneta"])
        self.assertEqual(fields["Categoria[ordem_exibicao_app]"], ["2"])
        self.assertEqual(fields["Categoria[exibir_app]"], ["0"])

    def test_sincronizar_icone(self):
        fields = _CategoriaFormParser.parse(FORM_FIXTURE)
        _sincronizar_icone_para_post(fields, FORM_FIXTURE)
        self.assertEqual(fields["Categoria[icone_app]"], ["isometrico_comum1_amarelo"])
        self.assertEqual(fields["Categoria[eh_icone_personalizado]"], ["0"])


if __name__ == "__main__":
    unittest.main()
