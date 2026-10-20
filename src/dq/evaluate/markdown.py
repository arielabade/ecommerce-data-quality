"""Resultado da avaliação -> ``reports/EVALUATION.md`` (tabelas completas, sem narrativa).

Papel: deixar no repositório, em texto, todos os números que o README e o
dashboard citam, para conferência.
Entrada: dicionário de ``dq.evaluate.__main__``.
Saída: Markdown.
"""

from __future__ import annotations

import math


def br(v, nd: int = 0) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "—"
    return f"{v:,.{nd}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def pct(v, nd: int = 1) -> str:
    return "—" if v is None or (isinstance(v, float) and math.isnan(v)) else br(v * 100, nd) + "%"


def _table(rows: list[dict], cols: list[tuple[str, str, callable]]) -> str:
    head = "| " + " | ".join(c[1] for c in cols) + " |"
    sep = "| " + " | ".join("---" if i == 0 else "---:" for i, _ in enumerate(cols)) + " |"
    body = ["| " + " | ".join(f(r.get(k)) for k, _, f in cols) + " |" for r in rows]
    return "\n".join([head, sep, *body])


DET = [("tecnica", "Técnica", str), ("sinalizados", "Sinalizados", br), ("acertos", "Acertos", br),
       ("erros_existentes", "Erros", br), ("precisao", "Precisão", pct), ("recall", "Recall", pct),
       ("legitimos_derrubados", "Legítimos derrubados", br)]


def render(o: dict) -> str:
    out = ["# Avaliação da limpeza", "",
           f"Perfil `{o['perfil']}`. Gerado por `make evaluate`. Dados simulados.", ""]
    titles = {"valores_pedido": "Valores de pedido (frete, desconto, total)", "precos_item": "Preços e quantidades de item",
              "serie_diaria": "Série diária de receita", "duplicatas": "Duplicatas", "nao_vendas": "Pedidos de teste e de bot"}
    for k, t in titles.items():
        cols = DET + ([("valor_legitimo_derrubado", "Receita legítima derrubada (R$)", lambda v: br(v, 0)),
                       ("distorcao_nao_detectada", "Distorção que passou (R$)", lambda v: br(v, 0))]
                      if k == "valores_pedido" else [])
        out += [f"## Detecção: {t}", "", _table(o["deteccao"][k], cols), ""]
    out += ["## Correções: o valor limpo bate com a verdade?", "",
            _table(o["correcoes"], [("tipo_defeito", "Defeito", str), ("registros", "Registros", br),
                                    ("restaurados_exatos", "Restaurados exatos", br), ("valor_diferente", "Valor diferente", br),
                                    ("fora_do_clean", "Fora do clean", br), ("taxa_restauracao", "Taxa", pct),
                                    ("erro_medio_antes", "Erro médio antes", lambda v: br(v, 2)),
                                    ("erro_medio_depois", "Erro médio depois", lambda v: br(v, 2))]), "",
            "Erro médio em R$ (valores), horas (datas) ou unidades (quantidade), contra o gold.", "",
            "Efeito colateral (valores sem defeito alterados pela limpeza):", "",
            _table(o["efeitos_colaterais"], [("tabela", "Tabela", str), ("coluna", "Coluna", str),
                                            ("valores_sem_defeito", "Sem defeito", br),
                                            ("alterados_indevidamente", "Alterados indevidamente", br)]), "",
            f"Unificação de clientes: {o['unificacao_clientes']}", "",
            "## Quarentena", "",
            _table(o["quarentena"], [("defeito", "Situação", str), ("registros", "Registros", br),
                                     ("em_quarentena", "Em quarentena", br), ("recall", "Recall", pct)]), "",
            "## Nulos: comparação de métodos (`fretes.custo_real`)", "",
            _table(o["imputacao"], [("metodo", "Método", str), ("mecanismo", "Mecanismo", str), ("linhas", "Linhas", br),
                                    ("erro_abs_medio_reais", "Erro abs. médio (R$)", lambda v: br(v, 2)),
                                    ("vies_medio_reais", "Viés", lambda v: br(v, 3))]), "",
            "## Métricas: verdade x antes x depois", "", "| Métrica | Verdade | Antes | Depois |", "| --- | ---: | ---: | ---: |"]
    m = o["metricas"]
    for key, label, f in (("receita", "Receita paga (R$)", lambda v: br(v, 0)), ("pedidos", "Pedidos", br),
                          ("pedidos_pagos", "Pedidos pagos", br), ("ticket_medio", "Ticket médio (R$)", lambda v: br(v, 2)),
                          ("taxa_cancelamento", "Cancelamento", lambda v: pct(v, 2)),
                          ("taxa_recompra", "Recompra (clientes 2+)", lambda v: pct(v, 1)),
                          ("pedidos_recorrentes_pct", "Pedidos de recompra", lambda v: pct(v, 1)),
                          ("clientes_compradores", "Clientes compradores", br),
                          ("categorias_distintas", "Categorias distintas", br)):
        out.append(f"| {label} | {f(m['verdade'][key])} | {f(m['antes'][key])} | {f(m['depois'][key])} |")
    wd = o["decisao_errada"]
    out += ["", "## Decisão errada: IQR global", ""]
    for k, label in (("remover_pedidos_iqr", "Remover pedidos acima da cerca do IQR"),
                     ("winsorizar_dia_iqr", "Limitar a receita diária na cerca do IQR"),
                     ("remover_dias_iqr", "Apagar os dias acima da cerca do IQR")):
        d = wd[k]
        out.append(f"- **{label}:** mês mais afetado {d['mes_mais_afetado']} ({pct(d['erro_mes_mais_afetado'])}); "
                   f"novembros: {', '.join(f'{mm} {pct(v)}' for mm, v in d['novembros'].items())}; "
                   f"média dos outros meses {pct(d['erro_medio_outros_meses'])}.")
    for e, v in wd.get("eventos_legitimos", {}).items():
        out.append(f"- Evento legítimo **{e}** (R$ {br(v['receita'], 0)}): removido {pct(v['removida_pedidos_iqr'])} "
                   f"apagando pedidos pelo IQR; {pct(v['removida_dias_iqr'])} apagando dias pelo IQR.")
    out += ["", "## Escalas para modelagem (ticket por pedido)", "",
            _table(o["escalas"], [("escala", "Escala", str), ("assimetria", "Assimetria", lambda v: br(v, 2)),
                                  ("pct_no_primeiro_decimo_da_faixa", "% no 1º décimo da faixa", pct),
                                  ("outliers_por_z_robusto_3_5", "% com |z robusto| > 3,5", pct)]), ""]
    return "\n".join(out)
