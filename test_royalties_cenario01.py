"""Testes offline do cálculo de royalties (cenário 01) e dos parsers HTTP da Machine.

Rodar: python3 -m pytest test_royalties_cenario01.py -q   (ou python3 test_royalties_cenario01.py)
"""

from datetime import date

from machine_financeiro_http import (
    calcular_boleto_ubiz,
    calcular_cenario,
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


def test_alfenas_outubro_igual_ao_financeiro():
    """Conferido com o cálculo manual do financeiro Ubiz (Alfenas, 10/2026): R$ 2.803,44(5)."""
    dem = parse_demonstrativo(DEMONSTRATIVO_HTML)
    entradas = {
        "ganhos_gerais": 126520.10,
        "taxa_central_pct": 15,
        "corridas_finalizadas": 8238,
        "boleto_machine": dem["valor_a_pagar"],
        "itens_demonstrativo": dem["itens"],
    }
    cfg = {"taxa_valor": 20, "taxa_tipo": "percentual", "taxa_seguro_app": 0.15, "taxa_seguro_app_tipo": "fixo",
           "tarifa_boleto": 3.50, "tarifa_boleto_tipo": "fixo"}
    c = calcular_cenario_01(entradas, cfg)
    assert c["saldo_01"] == 18978.01
    assert c["boleto_ubiz"] == 3739.09            # 8238×0,33 + 3985×0,17 + 206×0,14 + 8073×0,02 + 7465×0,02 + 3,50
    assert c["boleto_machine"] == 3134.97         # só referência
    assert c["seguro_app"] == 1239.20             # 8238×0,15 + 3,50
    assert c["base_royalties"] == 13999.72
    assert c["royalty"] == 2799.94
    assert c["valor_final"] == 2803.44            # financeiro arredondou para 2.803,45
    assert c["avisos"] == []


def test_item_desconhecido_usa_valor_machine():
    itens = [{"descricao": "Corridas finalizadas", "quantidade": 10, "debito": 2.7},
             {"descricao": "Coisa nova", "quantidade": 2, "debito": 1.0}]
    b = calcular_boleto_ubiz(itens)
    assert b["total"] == round(10 * 0.33 + 1.0 + 3.5, 2) and b["avisos"]


def test_cenario_02_taxa_fixa_por_corrida():
    """Passos: Taxa da central R$2,50 fixa → corridas × 2,50 − boleto Ubiz, × % Ubiz, + tarifa. Sem seguro."""
    t = parse_taxas(TAXAS_HTML.replace("15% - R$5,00", "R$2,50"))
    assert t["taxa_central"]["percentual"] is None and t["taxa_central"]["valor_fixo"] == 2.5
    itens = [{"descricao": "Corridas finalizadas", "quantidade": 16322, "debito": 0},
             {"descricao": "Corridas não finalizadas", "quantidade": 4000, "debito": 0}]
    entradas = {"cenario": "cenario_02", "ganhos_gerais": 0, "taxa_central_fixo": 2.5,
                "corridas_finalizadas": 16322, "itens_demonstrativo": itens, "boleto_machine": 0}
    c = calcular_cenario(entradas, {"taxa_valor": 20, "taxa_seguro_app": 0.5, "taxa_seguro_app_tipo": "fixo",
                                    "tarifa_boleto": 3.5})
    assert c["saldo_01"] == 40805.00                         # 16.322 × 2,50
    assert c["boleto_ubiz"] == round(16322 * 0.33 + 4000 * 0.17 + 3.5, 2)
    assert c["seguro_app"] == 0                              # cenário 2 não desconta seguro
    assert c["base_royalties"] == round(40805 - c["boleto_ubiz"], 2)
    assert c["valor_final"] == round(c["base_royalties"] * 0.2 + 3.5, 2)
    # dá para forçar o desconto do seguro, se o financeiro pedir
    c2 = calcular_cenario(entradas, {"taxa_valor": 20, "taxa_seguro_app": 0.5, "taxa_seguro_app_tipo": "fixo",
                                     "tarifa_boleto": 3.5, "descontar_seguro": True})
    assert c2["seguro_app"] == round(16322 * 0.5 + 3.5, 2)


class _RespCSV:
    def __init__(self, texto, ct="text/csv; charset=UTF-8"):
        self.content = texto.encode("latin-1")
        self.headers = {"Content-Type": ct}
        self.status_code = 200
        self.url = "https://cloud.taximachine.com.br/x"
        self.text = texto


def test_csv_cartao_latin1_soma_liquido_bandeira():
    from unittest import mock
    from machine_financeiro_http import obter_cartao_liquido
    csv = ("Nº OS;Bandeira;Categoria;Bandeira da corrida;Situação do Pagamento;Solicitação;Passageiro;"
           "Telefone do passageiro;Motorista;Valor da corrida;Valor líquido da bandeira;Valor líquido do motorista;"
           "Data esperada do pagamento;ID Transação Zoop\r\n"
           "1;Ubiz Car Brasil;X;Ubiz Car - Mariana;Paga;01/10/2026;Júlia;+55;Romário;36,60;34,770000000;0;01/10/2026;a\r\n"
           "2;Ubiz Car Brasil;X;Ubiz Car - Mariana;Em disputa;02/10/2026;Ana;+55;Carlos;1.036,90;1.035,050000000;0;02/10/2026;b\r\n")
    class H:
        def post(self, *a, **k):
            return _RespCSV(csv)
    r = obter_cartao_liquido(H(), "1875", "3085", "2026-10")
    assert r["total"] == 1069.82 and r["linhas"] == 2
    assert r["situacoes"] == {"Paga": 1, "Em disputa": 1}


def test_csv_vouchers_ticket_ponto_decimal():
    from machine_financeiro_http import obter_vouchers_tickets
    csv = "Motorista;VTR;Voucher;Ticket\r\nAGELSON;2;;20.80\r\nAlessandro;6;;288.47\r\nRyan;;;1151.51\r\n"
    class H:
        def post(self, *a, **k):
            return _RespCSV(csv)
    r = obter_vouchers_tickets(H(), "2026-02")
    assert r["ticket"] == 1460.78 and r["motoristas"] == 3


def test_cenario_03_mariana_video():
    """Números do vídeo (Mariana, fev/2026). Seguro: 1.000 corridas × 0,15 + 3,50 só para o teste."""
    extrato = {"recarga_central": 544.39, "recarga_pix": 58898.68, "cupom": 10.0, "desafios": 0, "indicacoes": 3.0,
               "cashback": 0, "saque_central": 0, "saque_pix": 1307.88,
               "cartao": 21840.77, "ticket": 2741.91, "tx_central": 84195.05}  # cartao/ticket do extrato NÃO entram
    e = {"cenario": "cenario_03", "extrato": extrato, "cartao_liquido": 25353.02, "eticket": 2915.16,
         "corridas_finalizadas": 1000}
    c = calcular_cenario(e, {"taxa_valor": 10, "taxa_seguro_app": 0.15, "taxa_seguro_app_tipo": "fixo", "tarifa_boleto": 3.5})
    assert c["total_entradas"] == round(544.39 + 58898.68 + 10 + 3 + 25353.02 + 2915.16, 2) == 87724.25
    assert c["seguro_app"] == 153.50
    assert c["total_saidas"] == round(1307.88 + 153.50, 2)
    assert c["base_royalties"] == round(87724.25 - 1461.38, 2)
    assert c["royalty"] == round(c["base_royalties"] * 0.10, 2)
    assert c["tarifa_boleto"] == 0 and c["valor_final"] == c["royalty"]   # sem tarifa por padrão


def test_cenario_04_taxa_central_mais_taxa_cartao():
    e = {"cenario": "cenario_04", "extrato": {"tx_central": 50000.0, "cartao": 59184.0}, "cartao_liquido": 76143.0,
         "corridas_finalizadas": 0}
    c = calcular_cenario(e, {"taxa_valor": 10, "descontar_seguro": False})
    assert c["taxa_cartao"] == 16959.0                         # 76.143 − 59.184 (vídeo)
    assert c["base_royalties"] == 66959.0
    assert c["valor_final"] == 6695.9
    c2 = calcular_cenario(e, {"taxa_valor": 10, "tarifa_boleto": 3.5, "somar_tarifa_boleto": True, "descontar_seguro": False})
    assert c2["valor_final"] == 6699.4


def test_cenario_04_itapetininga_igual_ao_financeiro():
    """Conferido com o financeiro (Itapetininga 10/2026): (1.296,72 − 68,00) × 30% = R$ 368,62."""
    e = {"cenario": "cenario_04", "extrato": {"tx_central": 1296.72, "cartao": 0}, "cartao_liquido": 0,
         "corridas_finalizadas": 430}
    c = calcular_cenario(e, {"taxa_valor": 30, "taxa_seguro_app": 0.15, "taxa_seguro_app_tipo": "fixo", "tarifa_boleto": 3.5})
    assert c["seguro_app"] == 68.00
    assert c["base_royalties"] == 1228.72
    assert c["valor_final"] == 368.62
    assert c["tarifa_boleto"] == 0


def test_datas():
    assert mes_passado_sp(date(2026, 11, 5)) == "2026-10"
    assert mes_passado_sp(date(2027, 1, 3)) == "2026-12"
    assert periodo_rotulo("2026-10") == "Referente a outubro de 2026"


if __name__ == "__main__":
    for nome, fn in list(globals().items()):
        if nome.startswith("test_"):
            fn()
            print("ok", nome)
