"""Pipeline da camada clean: staging -> regras -> tabelas prontas para carga.

Papel: aplicar os módulos de limpeza na ordem em que um depende do outro.
Entrada: tabelas do raw (texto) e ``config/cleaning.yaml``.
Saída: ``CleanResult`` com staging, clean, pedidos a remover, candidatos a
quarentena, o ``TreatmentLog`` e diagnósticos (nulos, fuso).
Dependências: ``dq.clean.*``.

Ordem e por quê:
1. staging (tipos) — todas as regras comparam números e instantes, não texto;
2. duplicatas exatas — antes de tudo, para nenhuma regra contar a mesma linha 2x;
3. texto (e-mail, endereço, categoria) — a unificação de clientes depende do
   e-mail canônico;
4. clientes duplicados — antes das duplicatas aproximadas, que comparam pedidos
   *do mesmo cliente*;
5. datas — antes das janelas de tempo (bot, duplicata), que dependem do horário certo;
6. testes e bots — saem antes do resto para não contaminar as estatísticas;
7. duplicatas aproximadas;
8. valores (itens, desconto, frete, total) — estatísticas robustas já sem lixo;
9. nulos — o modelo de imputação treina em valores já corrigidos;
10. sinais de outlier (só sinalizam).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from dq.clean import business_rules, dates, duplicates, freight, nulls, outliers, suspicious, text
from dq.clean.audit import TreatmentLog
from dq.clean.staging import local_date, stage_all


@dataclass
class CleanResult:
    staging: dict[str, pd.DataFrame]
    clean: dict[str, pd.DataFrame]
    excluded: pd.DataFrame                      # pedido_id + motivo (teste, bot, duplicata)
    quarantine_ids: dict[str, set] = field(default_factory=dict)   # motivo -> pedido_ids
    log: TreatmentLog = field(default_factory=TreatmentLog)
    diagnostics: dict = field(default_factory=dict)


def run(raw: dict[str, pd.DataFrame], cfg: dict) -> CleanResult:
    log = TreatmentLog()
    stg = stage_all(raw)
    t = {k: v.copy() for k, v in stg.items()}

    for name in ("pedidos", "itens_pedido", "fretes", "clientes", "enderecos"):
        t[name] = duplicates.drop_exact(t[name], name, log)

    t["clientes"] = text.standardize_emails(t["clientes"], log, cfg["texto"]["remover_alias_email"])
    t["enderecos"] = text.AddressStandardizer(cfg["texto"]["fuzzy_cidade_min"]).run(t["enderecos"], log)
    t["produtos"] = text.standardize_categories(t["produtos"], t["categorias"], log, cfg["texto"]["fuzzy_categoria_min"])

    t["clientes"], t["pedidos"] = duplicates.unify_customers(t["clientes"], t["pedidos"], log)

    t["pedidos"], unrecoverable = dates.fix_future_dates(t["pedidos"], log)
    t["pedidos"], tz_stats = dates.fix_timezone_segments(t["pedidos"], cfg["datas"], log)
    t["fretes"] = dates.fix_delivery_order(t["fretes"], t["pedidos"], cfg["datas"], log)

    test_ids = suspicious.test_orders(t["pedidos"], t["clientes"], cfg["pedidos_teste"], log)
    bot_ids = suspicious.bot_orders(t["pedidos"][~t["pedidos"]["pedido_id"].isin(test_ids)], cfg["bots"], log)
    gone = test_ids | bot_ids
    excluded = [(i, "pedido_teste") for i in sorted(test_ids)] + [(i, "bot") for i in sorted(bot_ids - test_ids)]
    t["pedidos"] = t["pedidos"][~t["pedidos"]["pedido_id"].isin(gone)]
    for name in ("itens_pedido", "fretes"):
        t[name] = t[name][~t[name]["pedido_id"].isin(gone)]

    before = set(t["pedidos"]["pedido_id"])
    t["pedidos"], t["itens_pedido"], t["fretes"] = duplicates.drop_near(
        t["pedidos"], t["itens_pedido"], t["fretes"], cfg["duplicatas"], log)
    excluded += [(i, "duplicata_aproximada") for i in sorted(before - set(t["pedidos"]["pedido_id"]))]

    t["itens_pedido"] = business_rules.fix_items(t["itens_pedido"], t["pedidos"], cfg["precos"], log)
    t["pedidos"] = business_rules.fix_discount(t["pedidos"], log)
    t["pedidos"], t["fretes"] = freight.reconcile_freight(t["pedidos"], t["fretes"], t["enderecos"], cfg["frete"], log)
    t["pedidos"] = business_rules.recompute_totals(t["pedidos"], log)
    business_rules.check_subtotal_vs_items(t["pedidos"], t["itens_pedido"], log)

    t["fretes"], null_diag = nulls.impute_freight_cost(t["fretes"], cfg["nulos"]["metodo_custo_frete"], log)
    t["pedidos"] = flag_outliers(t["pedidos"], t["clientes"], cfg["outliers"], log)

    return CleanResult(
        staging=stg, clean=t,
        excluded=pd.DataFrame(excluded, columns=["pedido_id", "motivo"]),
        quarantine_ids={"data_irrecuperavel": unrecoverable},
        log=log,
        diagnostics={"nulos_custo_real": null_diag, "fuso_segmentos": tz_stats},
    )


def flag_outliers(ped: pd.DataFrame, cli: pd.DataFrame, cfg: dict, log: TreatmentLog) -> pd.DataFrame:
    """Ticket fora do padrão *do tipo de cliente* (z robusto do log). Só sinaliza.

    Pedido grande de confecção é o negócio funcionando; o sinal existe para
    alguém olhar, não para a receita encolher.
    """
    ped = ped.copy()
    tipo = ped["cliente_id"].map(cli.set_index("cliente_id")["tipo_cliente"]).fillna("desconhecido")
    z = outliers.grouped_robust_z(np.log1p(ped["total_centavos"].clip(lower=0).astype(float)), tipo)
    # Só a flag vai para o clean: o z em si muda um pouco a cada dado novo e
    # transformaria toda carga incremental em atualização de todas as linhas.
    ped["outlier_ticket"] = (z.abs() > cfg["mad_z"]).fillna(False)
    log.add_many("pedidos", ped.loc[ped["outlier_ticket"], "pedido_id"], "total", "ticket_atipico_para_o_segmento",
                 "sinalizado", ped.loc[ped["outlier_ticket"], "total_centavos"])
    ped["total_winsor_centavos"] = outliers.winsorize(ped["total_centavos"].astype(float), cfg["winsor_quantil"], tipo).round().astype("Int64")
    ped["data_local"] = local_date(ped["criado_em"])
    return ped
