"""Dashboard estático (GitHub Pages): ``docs/dashboard/index.html``.

Papel: transformar a avaliação num documento para alguém de negócio: quanto da
base estava suja, o que cada técnica pegou e errou, erro x evento real, impacto
nas métricas e a recomendação. Títulos dizem a conclusão; o método fica num
bloco recolhível.
Entrada: ``<paths.reports>/evaluation.json`` e o relatório de qualidade da
última execução do ETL.
Saída: ``<paths.dashboard>/index.html`` (dados embutidos, Plotly via CDN) e
``data.json`` ao lado.
Dependências: stdlib; o template fica em ``dq/report/assets/dashboard.html``.
"""

from __future__ import annotations

import json
from pathlib import Path

from dq.settings import Settings

ASSETS = Path(__file__).parent / "assets"

FAMILIAS = {
    "Duplicatas": ["duplicata_exata", "reenvio_de_lote", "duplicata_aproximada"],
    "Teste e bot": ["pedido_teste", "pedido_bot"],
    "Valores (preço, frete, desconto)": ["frete_absurdo", "desconto_maior_que_subtotal", "frete_peso_incoerente",
                                         "preco_zero", "preco_negativo", "preco_separador_perdido", "quantidade_negativa"],
    "Datas e fuso": ["data_futura", "fuso_utc_sem_marcacao", "entrega_antes_da_compra"],
    "Integridade (órfãos)": ["pedido_ausente", "item_orfao", "frete_orfao", "pedido_orfao_cliente", "cliente_ausente"],
    "Chegada atrasada": ["chegada_atrasada"],
    "Cadastro e texto": ["email_sujo", "conta_duplicada", "cidade_grafia", "uf_grafia", "cep_formato", "cep_de_outra_uf",
                         "categoria_grafia"],
    "Nulos": ["nulo_mcar", "nulo_mar", "nulo_mnar"],
    "Formato (só parsing)": ["formato_moeda_br", "formato_data_br", "formato_data_iso_utc"],
}

ROTULOS = {
    "duplicata_exata": "Pedido repetido idêntico", "reenvio_de_lote": "Dia inteiro reenviado",
    "duplicata_aproximada": "Checkout reenviado segundos depois", "pedido_teste": "Pedido de teste da equipe",
    "pedido_bot": "Pedido de bot (teste de cartão)", "frete_absurdo": "Frete absurdo (x100, 9.999,99)",
    "desconto_maior_que_subtotal": "Desconto maior que o preço", "frete_peso_incoerente": "Frete trocado entre pedidos",
    "preco_zero": "Preço zero", "preco_negativo": "Preço negativo", "preco_separador_perdido": "Vírgula do preço perdida",
    "quantidade_negativa": "Quantidade negativa", "data_futura": "Data no futuro", "fuso_utc_sem_marcacao": "Fuso trocado (WhatsApp)",
    "entrega_antes_da_compra": "Entrega antes da compra", "pedido_ausente": "Pedido perdido (itens órfãos)",
    "item_orfao": "Item sem pedido", "frete_orfao": "Frete sem pedido", "pedido_orfao_cliente": "Pedido sem cliente",
    "cliente_ausente": "Cliente perdido", "chegada_atrasada": "Pedido que chegou dias depois", "email_sujo": "E-mail sujo",
    "conta_duplicada": "Mesma pessoa em duas contas", "cidade_grafia": "Cidade com grafia diferente",
    "uf_grafia": "UF com grafia diferente", "cep_formato": "CEP em outro formato", "cep_de_outra_uf": "CEP de outra UF",
    "categoria_grafia": "Categoria com grafia diferente", "nulo_mcar": "Custo de frete ausente (ao acaso)",
    "nulo_mar": "Custo de frete ausente (por transportadora)", "nulo_mnar": "Custo de frete ausente (os mais caros)",
    "formato_moeda_br": "Moeda em formato brasileiro", "formato_data_br": "Data dd/mm/aaaa",
    "formato_data_iso_utc": "Data em UTC explícito",
}


def build_payload(ev: dict, quality: dict | None) -> dict:
    por_tipo = {r["tipo_defeito"]: r["linhas"] for r in ev["registro"]["por_tipo"] if not r["legitimo"]}
    sujeira = []
    for fam, tipos in FAMILIAS.items():
        for t in tipos:
            if t in por_tipo:
                sujeira.append({"familia": fam, "tipo": t, "rotulo": ROTULOS.get(t, t), "linhas": por_tipo[t]})
    m = ev["metricas"]
    vd, an, dp = m["verdade"], m["antes"], m["depois"]
    det = ev["deteccao"]
    combo = next(r for r in det["valores_pedido"] if "ETL" in r["tecnica"])
    iqr = next(r for r in det["valores_pedido"] if r["tecnica"] == "IQR global")
    qv = {r["motivo"]: r for r in ev["quarentena_valor"]}
    em_quarentena = sum(r["valor"] for r in ev["quarentena_valor"] if r["motivo"] in
                        ("atraso_fora_da_janela", "pedido_sem_cliente", "data_irrecuperavel"))
    return {
        "kpis": {
            "receita_verdade": vd["receita"], "receita_antes": an["receita"], "receita_depois": dp["receita"],
            "erro_antes": an["receita"] / vd["receita"] - 1, "erro_depois": dp["receita"] / vd["receita"] - 1,
            "pedidos_raw": ev["registro"]["pedidos_raw"], "pedidos_com_defeito": ev["registro"]["pedidos_com_defeito"],
            "defeitos": ev["registro"]["defeitos"], "combo": combo, "iqr": iqr,
            "receita_quarentena": em_quarentena,
            "pedidos_perdidos_valor": qv.get("item_sem_pedido", {}).get("valor", 0.0),
        },
        "metricas": m, "sujeira": sujeira, "deteccao": det, "serie": ev["serie_diaria"],
        "decisao": ev["decisao_errada"], "mensal": ev["mensal"], "regional": ev["regional"],
        "correcoes": ev["correcoes"], "imputacao": ev["imputacao"], "escalas": ev["escalas"],
        "quarentena": ev["quarentena"], "quarentena_valor": ev["quarentena_valor"],
        "unificacao": ev["unificacao_clientes"],
        "etl": None if quality is None else {
            "run_id": quality["run_id"], "idempotente": quality["idempotente"], "tratamento": quality["tratamento"],
            "excluidos": quality["excluidos"], "totais": quality["totais_carga"],
        },
    }


def latest_quality(s: Settings) -> dict | None:
    runs = sorted(s.path("runs").glob("r*/quality_report.json"))
    return json.loads(runs[-1].read_text(encoding="utf-8")) if runs else None


def write_dashboard(s: Settings) -> Path:
    ev = json.loads((s.path("reports") / "evaluation.json").read_text(encoding="utf-8"))
    payload = build_payload(ev, latest_quality(s))
    out = s.path("dashboard")
    out.mkdir(parents=True, exist_ok=True)
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    html = (ASSETS / "dashboard.html").read_text(encoding="utf-8").replace("/*__DATA__*/null", data)
    (out / "index.html").write_text(html, encoding="utf-8")
    (out / "data.json").write_text(data, encoding="utf-8")
    return out / "index.html"
