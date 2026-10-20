"""Pedidos que não são vendas: testes da equipe e rajadas de bot.

Papel: tirar da base de métricas os pedidos que nunca representaram demanda.
Entrada: pedidos e clientes de staging, parâmetros ``pedidos_teste`` e ``bots``.
Saída: listas de pedido_id a remover + ``TreatmentLog``.
Dependências: pandas, numpy.

- **Teste:** conta interna (domínio da loja ou nome "teste", "homologação",
  "QA") ou total até R$ 1,00. O menor pedido real da origem custa R$ 4,45; um
  pedido de R$ 1,00 ou R$ 0,01 é teste de gateway.
- **Bot:** velocidade. Muitos pedidos do mesmo IP (ou da mesma conta) em poucos
  minutos. Card testing gera dezenas de pedidos recusados; deixá-los na base
  infla a taxa de cancelamento e o número de pedidos. O maior cliente real (um
  B2B) faz no máximo 2 pedidos por hora, então o limite de 8 em 15 minutos tem
  folga dos dois lados.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from dq.clean.audit import TreatmentLog


def test_orders(ped: pd.DataFrame, cli: pd.DataFrame, cfg: dict, log: TreatmentLog) -> set[int]:
    dom = "|".join(d.replace(".", r"\.") for d in cfg["dominios_internos"])
    internal = cli["email"].str.contains(rf"@(?:{dom})$", regex=True, na=False) | \
        cli["nome"].str.contains(cfg["padrao_nome"], regex=True, na=False)
    staff = set(cli.loc[internal, "cliente_id"])
    by_account = ped["cliente_id"].isin(staff)
    by_value = ped["total_centavos"].le(cfg["total_maximo_centavos"]).fillna(False) & ped["total_centavos"].ge(0)
    hit = by_account | by_value
    log.add_many("pedidos", ped.loc[by_account, "pedido_id"], "*", "pedido_teste_conta_interna", "removido")
    log.add_many("pedidos", ped.loc[by_value & ~by_account, "pedido_id"], "*", "pedido_teste_valor_simbolico",
                 "removido", None, ped.loc[by_value & ~by_account, "total_centavos"])
    return set(int(x) for x in ped.loc[hit, "pedido_id"])


def velocity_bursts(ped: pd.DataFrame, by: str, window_min: float, min_count: int) -> set[int]:
    """Pedidos que fazem parte de uma janela de ``window_min`` com ``min_count``+ pedidos da mesma origem."""
    p = ped[["pedido_id", by, "criado_em"]].dropna().sort_values([by, "criado_em"])
    ts = p["criado_em"].astype("int64").to_numpy() // 1_000_000
    key = p[by].to_numpy()
    ids = p["pedido_id"].to_numpy()
    hit = np.zeros(len(p), bool)
    w = window_min * 60
    start = 0
    for end in range(len(p)):
        if key[end] != key[start]:
            start = end
        while ts[end] - ts[start] > w:
            start += 1
        if end - start + 1 >= min_count:
            hit[start:end + 1] = True
    return set(int(x) for x in ids[hit])


def bot_orders(ped: pd.DataFrame, cfg: dict, log: TreatmentLog) -> set[int]:
    by_ip = velocity_bursts(ped, "ip_checkout", cfg["janela_minutos"], cfg["min_pedidos_por_ip"])
    by_cli = velocity_bursts(ped, "cliente_id", cfg["janela_minutos"], cfg["min_pedidos_por_cliente"])
    log.add_many("pedidos", sorted(by_ip), "*", "bot_rajada_ip", "removido")
    log.add_many("pedidos", sorted(by_cli - by_ip), "*", "bot_rajada_cliente", "removido")
    return by_ip | by_cli
