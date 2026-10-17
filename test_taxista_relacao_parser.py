"""Parser HTML do formulário taxista (sem rede)."""
import unittest

from machine_taxista_http import _TaxistaFormParser, descobrir_campo_relacao


FIXTURE = """
<html><body>
<form id="taxista-form" method="post" action="/taxista/update/99">
<input type="hidden" name="YII_CSRF_TOKEN" value="csrf-test" />
<input type="hidden" name="Taxista[id]" value="99" />
<input type="text" name="Taxista[nome]" value="João" />
<select name="Taxista[taxista_apoio]">
  <option value="0" selected>Motorista prioritário</option>
  <option value="1">Motorista secundário</option>
</select>
</form>
</body></html>
"""


class TaxistaRelacaoParserTests(unittest.TestCase):
    def test_parse_hidden_and_select(self):
        fields = _TaxistaFormParser.parse(FIXTURE)
        self.assertEqual(fields["YII_CSRF_TOKEN"], ["csrf-test"])
        self.assertEqual(fields["Taxista[id]"], ["99"])
        self.assertEqual(fields["Taxista[taxista_apoio]"], ["0"])

    def test_descobrir_campo_relacao(self):
        campo, mapping = descobrir_campo_relacao(FIXTURE)
        self.assertEqual(campo, "Taxista[taxista_apoio]")
        self.assertEqual(mapping["prioritario"], "0")
        self.assertEqual(mapping["secundario"], "1")


if __name__ == "__main__":
    unittest.main()
