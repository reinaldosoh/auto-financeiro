"""Testes offline do cálculo de royalties (cenário 01) e dos parsers HTTP da Machine.

Rodar: python3 -m pytest test_royalties_cenario01.py -q   (ou python3 test_royalties_cenario01.py)
"""

from datetime import date

from machine_financeiro_http import (
    calcular_cenario_01,
    mes_passado_sp,
    parse_centrais,
    parse_demonstrativo,
    parse_indicadores,
    parse_numero_br,
    parse_taxas,
    periodo_rotulo,
)

# HTML real de /demonstrativoMensal/index (Ubiz Car - Alfenas, competência 2026-10), capturado do painel.
DEMONSTRATIVO_HTML = """
<form method="post" action="/demonstrativoMensal/index">
<select name="FinCobrancaBandeira[bandeira_id]" id="FinCobrancaBandeira_bandeira_id">
<option value="1875">Ubiz Car Brasil</option><option value="2796" selected="selected">Ubiz Car - Alfenas</option>
</select>
<select name="data_ano_mes" id="data_ano_mes"><option value="2026-10" selected="selected">Outubro</option>
<option value="2026-09">Setembro</option></select>
</form>
<table class="table items"> <thead> <tr> <th id="col-descricao">Descrição</th> <th>Quantidade</th>
<th>Valor unitário</th> <th>Débitos</th> <th>Créditos</th> </tr> </thead> <tbody>
<tr> <td>Corridas finalizadas</td> <td>8238</td> <td> R$ 0,27 </td> <td>R$ 2.224,26</td> <td>---</td> </tr>
<tr> <td>Corridas não finalizadas</td> <td>3985</td> <td> R$ 0,14 </td> <td>R$ 557,90</td> <td>---</td> </tr>
<tr> <td>Valor corridas</td> <td>---</td> <td>---</td> <td>R$ 2.782,16</td> <td>---</td> </tr>
<tr> <td> Validação CPF </td> <td>206</td> <td> R$ 0,14 </td> <td>R$ 28,84</td> <td>---</td> </tr>
<tr> <td>Rota exibida durante a estimativa</td> <td>8073</td> <td>R$ 0,02085</td> <td>R$ 168,32</td> <td>---</td> </tr>
<tr> <td>Rota exibida durante a viagem</td> <td>7465</td> <td>R$ 0,02085</td> <td>R$ 155,65</td> <td>---</td> </tr>
<tr> <td>Total a pagar</td> <td>---</td> <td>---</td> <td>R$ 3.134,97</td> <td>---</td> </tr>
</tbody> </table>
<div class="total responsive-content" align="right"> <hr class="separator">
<div class="valor_total">Valor a pagar: R$ 3.134,97</div> </div>
"""

CENTRAIS_HTML = """
<script type="application/json" id="sso-centrais-iniciais">
[{"bandeira_id":1875,"bandeira_nome":"Ubiz Car Brasil","bandeira_cod_interno":"1859","cidade_nome":"Brasília","ehAtual":true},
 {"bandeira_id":2796,"bandeira_nome":"Ubiz Car - Alfenas","bandeira_cod_interno":"2796","cidade_nome":"Alfenas","ehAtual":false}]
</script>
"""

# Estrutura dos cartões da tela de taxas (button.short.btn-tax), valores do vídeo.
TAXAS_HTML = """
<button class="short btn-tax"><span class="short_title">Taxa da central</span>
<div class="taxa-content">15% - R$5,00</div>
<div class="categorias-content">Área Urbana, UBIZ PET, Corridas Whatsapp</div>
<div class="tipos-pagamento-content">Todas as formas de pagamento</div></button>
<button class="short btn-tax"><span class="short_title">Taxa App</span>
<div class="taxa-content">20% - R$5,00</div><div class="categorias-content">MAÇANETA</div></button>
<button class="short btn-tax"><span class="short_title">Taxa Seguro App</span>
<div class="taxa-content">R$0,20</div><div class="categorias-content">Todas as categorias</div></button>
"""


def test_parse_numero_br():
    assert parse_numero_br("R$ 238.840,54") == 238840.54
    assert parse_numero_br("R$ 0,02085") == 0.02085
    assert parse_numero_br("15%") == 15.0
    assert parse_numero_br(8238) == 8238.0
    assert parse_numero_br("---") == 0.0
    assert parse_numero_br("238840.54") == 238840.54


def test_demonstrativo():
    d = parse_demonstrativo(DEMONSTRATIVO_HTML)
    assert d["valor_a_pagar"] == 3134.97
    assert d["total_a_pagar"] == 3134.97
    assert d["bandeira_id"] == "2796" and d["ano_mes"] == "2026-10"
    fin = next(i for i in d["itens"] if i["descricao"] == "Corridas finalizadas")
    assert fin["quantidade"] == 8238 and fin["debito"] == 2224.26
    assert any(i["descricao"] == "Valor corridas" and i["subtotal"] for i in d["itens"])


def test_centrais():
    c = parse_centrais(CENTRAIS_HTML)
    assert [x["bandeira_id"] for x in c] == ["1875", "2796"]
    assert c[0]["atual"] and not c[1]["atual"]


def test_taxas():
    t = parse_taxas(TAXAS_HTML)
    assert t["taxa_central"]["percentual"] == 15.0 and t["taxa_central"]["valor_fixo"] == 5.0
    assert t["taxa_app"]["categorias"] == ["MAÇANETA"]
    assert t["taxa_seguro_app"]["percentual"] is None and t["taxa_seguro_app"]["valor_fixo"] == 0.2


def test_indicadores():
    payload = {
        "ganhoGeral": {"valor": "238.840,54", "grafico": [{"texto": "Dinheiro", "quantidade": "145.493,00"}]},
        "solicitada": {"valor": 19542, "grafico": [
            {"texto": "Finalizadas", "quantidade": 15304},
            {"texto": "Canceladas", "quantidade": 4085},
            {"texto": "Não atendidas", "quantidade": 153},
        ]},
        "origemFinalizada": {"valor": 15304},
    }
    i = parse_indicadores(payload)
    assert i["ganhos_gerais"] == 238840.54
    assert (i["corridas_finalizadas"], i["corridas_canceladas"], i["corridas_nao_atendidas"]) == (15304, 4085, 153)


def test_calculo_cenario_01():
    # Ganhos do vídeo + boleto do demonstrativo; royalty 10%, seguro R$0,20 fixo/corrida, tarifa boleto R$3,50.
    entradas = {"ganhos_gerais": 238840.54, "taxa_central_pct": 15, "boleto_machine": 3134.97, "corridas_finalizadas": 15304}
    cfg = {"taxa_valor": 10, "taxa_tipo": "percentual", "taxa_seguro_app": 0.20, "taxa_seguro_app_tipo": "fixo",
           "tarifa_boleto": 3.50, "tarifa_boleto_tipo": "fixo"}
    c = calcular_cenario_01(entradas, cfg)
    assert c["saldo_01"] == 35826.08                     # 238.840,54 × 15%
    assert c["seguro_app"] == 3064.30                    # 15.304 × 0,20 + 3,50
    assert c["total_deducoes"] == 6199.27                # 3.134,97 + 3.064,30
    assert c["base_royalties"] == 29626.81
    assert c["royalty"] == 2962.68                       # base × 10%
    assert c["valor_final"] == 2966.18                   # + tarifa boleto
    assert c["avisos"] == []


def test_calculo_seguro_global_e_avisos():
    entradas = {"ganhos_gerais": 1000, "taxa_central_pct": 15, "boleto_machine": 200, "corridas_finalizadas": 100}
    c = calcular_cenario_01(entradas, {"taxa_valor": 0, "taxa_seguro_app_global": 0.2, "taxa_seguro_app_global_tipo": "fixo"})
    assert c["seguro_app_detalhe"]["origem"] == "global" and c["seguro_app"] == 23.5
    assert c["base_royalties"] < 0
    assert any("negativa" in a for a in c["avisos"]) and any("royalty" in a for a in c["avisos"])


def test_datas():
    assert mes_passado_sp(date(2026, 11, 5)) == "2026-10"
    assert mes_passado_sp(date(2027, 1, 3)) == "2026-12"
    assert periodo_rotulo("2026-10") == "Referente a outubro de 2026"


if __name__ == "__main__":
    for nome, fn in list(globals().items()):
        if nome.startswith("test_"):
            fn()
            print("ok", nome)
