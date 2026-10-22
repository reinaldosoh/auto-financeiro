"""Parser HTML do formulário taxista (sem rede)."""
import unittest

from machine_taxista_http import (
    PainelErro,
    _TaxistaFormParser,
    _normalizar_telefone_taxista,
    atualizar_relacao_taxista,
    classificar_erro,
    descobrir_campo_relacao,
    diff_formularios,
    listar_taxistas,
    parse_grade_taxistas,
)


FIXTURE = """
<html><body>
<form id="taxista-form" method="post" action="/taxista/update/99">
<input type="hidden" name="YII_CSRF_TOKEN" value="csrf-test" />
<input type="hidden" name="Taxista[id]" value="99" />
<input type="text" name="Taxista[nome]" value="João" />
<select name="Taxista[taxista_apoio]">
  <option value="0">Motorista prioritário</option>
  <option value="1" selected="selected">Motorista secundário</option>
</select>
<button type="submit" name="yt0">Gravar</button>
</form>
</body></html>
"""


class TaxistaRelacaoParserTests(unittest.TestCase):
    def test_parse_hidden_and_select(self):
        fields = _TaxistaFormParser.parse(FIXTURE)
        self.assertEqual(fields["YII_CSRF_TOKEN"], ["csrf-test"])
        self.assertEqual(fields["Taxista[id]"], ["99"])
        self.assertEqual(fields["Taxista[taxista_apoio]"], ["1"])

    def test_descobrir_campo_relacao(self):
        campo, mapping = descobrir_campo_relacao(FIXTURE)
        self.assertEqual(campo, "Taxista[taxista_apoio]")
        self.assertEqual(mapping["prioritario"], "0")
        self.assertEqual(mapping["secundario"], "1")

    def test_normalizar_telefone(self):
        self.assertEqual(
            _normalizar_telefone_taxista("+55 31 98734-0913"),
            "(31) 98734-0913",
        )


def _html(apoio="1", extra="", apoio_attrs=""):
    sel0 = ' selected="selected"' if apoio == "0" else ""
    sel1 = ' selected="selected"' if apoio == "1" else ""
    return f"""
<form id="taxista-form" method="post" action="/taxista/update/99">
<input type="hidden" name="YII_CSRF_TOKEN" value="tok" />
<input type="text" name="Taxista[nome]" value="Jo&atilde;o &amp; Cia" />
<input type="tel" name="Taxista[telefone]" value="+55 31 98734-0913" />
<input type="hidden" name="full_phone" value="" />
<input type="text" name="ultima_atividade" value="agora" readonly />
<select name="Taxista[vinculo]" disabled><option value="" selected>Selecione</option>
<option value="T">Titular</option></select>
<select name="Taxista[taxista_apoio]"{apoio_attrs}>
  <option value="0"{sel0}>Motorista prioritário</option>
  <option value="1"{sel1}>Motorista secundário</option>
</select>
{extra}
<button type="submit" name="yt0">Gravar</button>
</form>"""


class _Resp:
    def __init__(self, text, status=200):
        self.text = text
        self.status_code = status


class _Http:
    """Painel falso: GET devolve o estado atual; POST aplica `on_post`."""

    def __init__(self, html, on_post=None, resposta_post=None):
        self.html = html
        self.on_post = on_post
        self.resposta_post = resposta_post
        self.posts = []

    def get(self, url, **kw):
        return _Resp(self.html)

    def post(self, url, data=None, **kw):
        self.posts.append(list(data))
        if self.on_post:
            self.html = self.on_post(self.html, dict(data))
        return _Resp(self.resposta_post if self.resposta_post is not None else self.html)


class ParserNavegadorTests(unittest.TestCase):
    def test_disabled_nao_e_enviado_e_e_listado(self):
        ign = []
        f = _TaxistaFormParser.parse(_html(), ign)
        self.assertNotIn("Taxista[vinculo]", f)
        self.assertEqual(ign, ["Taxista[vinculo]"])
        self.assertIn("ultima_atividade", f)  # readonly é enviado

    def test_entidades_html_decodificadas(self):
        f = _TaxistaFormParser.parse(_html())
        self.assertEqual(f["Taxista[nome]"], ["João & Cia"])

    def test_aspas_simples_checkbox_e_multiselect(self):
        extra = (
            "<input type='text' name='a' value='x y'>"
            '<input type="checkbox" name="c" value="" checked>'
            '<input type="checkbox" name="d" checked>'
            '<input type="checkbox" name="e" value="1">'
            '<select name="m[]" multiple><option value="1" selected>a</option>'
            '<option value="2" selected>b</option><option value="3">c</option></select>'
        )
        f = _TaxistaFormParser.parse(_html(extra=extra))
        self.assertEqual(f["a"], ["x y"])
        self.assertEqual(f["c"], [""])
        self.assertEqual(f["d"], ["on"])
        self.assertNotIn("e", f)
        self.assertEqual(f["m[]"], ["1", "2"])

    def test_diff_ignora_ruido(self):
        antes = {"a": ["1"], "ultima_atividade": ["x"], "YII_CSRF_TOKEN": ["t1"]}
        depois = {"a": ["2"], "ultima_atividade": ["y"], "YII_CSRF_TOKEN": ["t2"]}
        self.assertEqual(
            diff_formularios(antes, depois), [{"campo": "a", "antes": ["1"], "depois": ["2"]}]
        )


class TelefoneComoNavegadorTests(unittest.TestCase):
    def test_remove_codigo_do_pais_e_preenche_full_phone(self):
        f = {"Taxista[telefone]": ["+55 31 98734-0913"], "full_phone": [""]}
        auto = __import__("machine_taxista_http")
        auto._telefone_como_navegador(f)
        self.assertEqual(f["Taxista[telefone]"], ["31 98734-0913"])
        self.assertEqual(f["full_phone"], ["+5531987340913"])

    def test_telefone_ja_nacional_ou_vazio_nao_muda(self):
        auto = __import__("machine_taxista_http")
        for tel in ("31 98734-0913", "(31) 98734-0913", ""):
            f = {"Taxista[telefone]": [tel], "full_phone": ["+5531987340913"]}
            auto._telefone_como_navegador(f)
            self.assertEqual(f["Taxista[telefone]"], [tel])
            self.assertEqual(f["full_phone"], ["+5531987340913"])


class ClassificarErroTests(unittest.TestCase):
    def test_codigos(self):
        casos = {
            "Ocorreu um erro ao salvar as configurações de saldo mínimo.": ("saldo_minimo", 422),
            "sua bandeira está desativada": ("bandeira_desativada", 403),
            "o login pediu código 2FA, mas não há segredo salvo neste servidor": ("2fa_sem_segredo", 409),
            "HTTP 403 em /taxista/update — conta sem permissão.": ("sem_permissao", 403),
            "Sessão expirada — faça login novamente.": ("sessao_expirada", 401),
            "algo inesperado": ("erro_painel", 400),
        }
        for msg, esperado in casos.items():
            self.assertEqual(classificar_erro(RuntimeError(msg)), esperado, msg)
        self.assertEqual(classificar_erro(PainelErro("x", "m", 409)), ("x", 409))


class AtualizarRelacaoTests(unittest.TestCase):
    @staticmethod
    def _aplica(html, dados):
        novo = dados["Taxista[taxista_apoio]"]
        return _html(apoio=novo)

    def test_ja_no_valor_nao_posta(self):
        http = _Http(_html(apoio="1"))
        r = atualizar_relacao_taxista(http, 99, "secundario")
        self.assertTrue(r["inalterado"])
        self.assertEqual(http.posts, [])

    def test_dry_run_nao_posta_e_mostra_previsao(self):
        http = _Http(_html(apoio="1"))
        r = atualizar_relacao_taxista(http, 99, "prioritario", dry_run=True)
        self.assertEqual(http.posts, [])
        self.assertEqual(
            r["alteracoes_previstas"],
            [{"campo": "Taxista[taxista_apoio]", "antes": ["1"], "depois": ["0"]}],
        )
        self.assertEqual(r["campos_desabilitados_nao_enviados"], ["Taxista[vinculo]"])

    def test_grava_so_o_alvo_sem_vinculo_nem_telefone(self):
        http = _Http(_html(apoio="1"), on_post=self._aplica)
        r = atualizar_relacao_taxista(http, 99, "prioritario")
        enviados = dict(http.posts[0])
        self.assertEqual(enviados["Taxista[taxista_apoio]"], "0")
        self.assertNotIn("Taxista[vinculo]", enviados)
        # como o navegador: telefone nacional + full_phone internacional (o número não muda)
        self.assertEqual(enviados["Taxista[telefone]"], "31 98734-0913")
        self.assertEqual(enviados["full_phone"], "+5531987340913")
        self.assertEqual(enviados["Taxista[nome]"], "João & Cia")
        self.assertEqual(r["alteracoes_inesperadas"], [])
        self.assertEqual([a["campo"] for a in r["alteracoes"]], ["Taxista[taxista_apoio]"])

    def test_alteracao_inesperada_e_reportada(self):
        def aplica(html, d):
            return _html(apoio="0").replace("+55 31 98734-0913", "(31) 98734-0913")

        http = _Http(_html(apoio="1"), on_post=aplica)
        r = atualizar_relacao_taxista(http, 99, "prioritario")
        self.assertEqual([a["campo"] for a in r["alteracoes_inesperadas"]], ["Taxista[telefone]"])

    def test_painel_nao_gravou(self):
        http = _Http(_html(apoio="1"))
        with self.assertRaises(PainelErro) as ctx:
            atualizar_relacao_taxista(http, 99, "prioritario")
        self.assertEqual((ctx.exception.codigo, ctx.exception.status), ("painel_nao_gravou", 409))

    def test_erro_de_saldo_minimo_do_painel(self):
        html_erro = _html(apoio="1") + '<div class="errorSummary"><ul><li>Ocorreu um erro ao salvar as configurações de saldo mínimo.</li></ul></div>'
        http = _Http(_html(apoio="1"), resposta_post=html_erro)
        with self.assertRaises(PainelErro) as ctx:
            atualizar_relacao_taxista(http, 99, "prioritario")
        self.assertEqual(ctx.exception.codigo, "saldo_minimo")

    def test_select_desabilitado(self):
        http = _Http(_html(apoio="1", apoio_attrs=" disabled"))
        with self.assertRaises(PainelErro) as ctx:
            atualizar_relacao_taxista(http, 99, "prioritario")
        self.assertEqual(ctx.exception.codigo, "campo_desabilitado")


LINHA = (
    '<tr class="{cls}"> <td class="checkbox-column"><input onclick="selecionarTaxistaAlteracaoMassa(this)" '
    'value="{id}" id="alteracao-massiva-taxista-id_0" type="checkbox" name="alteracao-massiva-taxista-id[]"></td>'
    '<td style="display: none" class="col-data-hora-status">08/02/2023 12:10:38</td>'
    '<td class="col-nome">Fulano &amp; Tal</td><td>113</td><td class="col-email">f@example.com</td>'
    '<td style="" class="col-status">Ativo</td>'
    '<td class="col-categoria">Corrida Central<br> Ma\u00e7aneta<br> Ubiz</td>'
    '<td class="fixed-right"><a href="/taxista/update/{id}"> Editar </a></td></tr>'
)


def _grade(ids, total):
    linhas = "".join(
        LINHA.format(cls="odd" if i % 2 == 0 else "even", id=i_d) for i, i_d in enumerate(ids)
    )
    return (
        f'<div class="summary">Exibindo 1-{len(ids)} de {total} resultados.</div>'
        f'<table class="items table"><tbody>{linhas}</tbody></table>'
    )


class _HttpGrade:
    def __init__(self, total, por_pagina=10):
        self.total = total
        self.por = por_pagina
        self.posts = []

    def get(self, url, **kw):
        r = _Resp('<form id="taxista-form"><input name="TaxistaSearch[nome]" value="">'
                  '<select name="TaxistaSearch[taxista_apoio]"><option value="" selected>Selecione</option></select></form>')
        r.url = url
        return r

    def post(self, url, data=None, **kw):
        d = dict(data)
        pag = int(url.split("TaxistaSearch_page=")[1]) if "TaxistaSearch_page=" in url else 1
        self.posts.append((pag, d))
        ini = (pag - 1) * self.por
        ids = list(range(1000 + ini, 1000 + min(ini + self.por, self.total)))
        return _Resp(_grade(ids, self.total))


class ListarTaxistasTests(unittest.TestCase):
    def test_parse_grade(self):
        g = parse_grade_taxistas(_grade([772770, 5], 1594))
        self.assertEqual(g["total"], 1594)
        self.assertEqual(g["itens"][0]["taxista_id"], 772770)
        self.assertEqual(g["itens"][0]["nome"], "Fulano & Tal")
        self.assertEqual(g["itens"][0]["status"], "Ativo")
        self.assertEqual(g["itens"][0]["categorias"], ["Corrida Central", "Ma\u00e7aneta", "Ubiz"])

    def test_filtro_envia_relacao_e_status(self):
        http = _HttpGrade(52)
        r = listar_taxistas(http, relacao="secundario", status=["a", "I"], pagina=2)
        _, d = http.posts[0]
        self.assertEqual(d["TaxistaSearch[taxista_apoio]"], "1")
        self.assertEqual(http.posts[0][0], 2)
        self.assertEqual((r["total"], r["paginas"], r["pagina"], len(r["itens"])), (52, 6, 2, 10))

    def test_todas_percorre_paginas(self):
        http = _HttpGrade(25)
        r = listar_taxistas(http, relacao="prioritario", todas=True)
        self.assertEqual([p for p, _ in http.posts], [1, 2, 3])
        self.assertEqual(len(r["itens"]), 25)
        self.assertTrue(r["completo"])
        self.assertEqual(len({i["taxista_id"] for i in r["itens"]}), 25)

    def test_parametros_invalidos(self):
        for kw in ({"relacao": "x"}, {"status": ["Z"]}, {"pagina": 0}):
            with self.assertRaises(PainelErro):
                listar_taxistas(_HttpGrade(1), **kw)


if __name__ == "__main__":
    unittest.main()
