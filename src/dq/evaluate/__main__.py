"""``python -m dq.evaluate``: mede a limpeza contra o gabarito e o impacto no negócio.

Papel: juntar detecção, correção, quarentena, imputação, impacto, decisão
errada e escalas num único resultado.
Entrada: warehouse do ETL, registro de defeitos, gold.
Saída: ``<paths.reports>/evaluation.json`` (lido pelo dashboard),
``<paths.reports>/*.csv`` e ``<paths.reports>/EVALUATION.md``.
Dependências: ``dq.evaluate.*``.
"""

from __future__ import annotations

import json
import time

import duckdb
import numpy as np
import pandas as pd

from dq.evaluate import corrections, detection, impact, imputation
from dq.evaluate.markdown import render
from dq.evaluate.truth import Truth
from dq.settings import get_settings


def _records(df: pd.DataFrame) -> list[dict]:
    return json.loads(df.to_json(orient="records", force_ascii=False))


def main(settings=None) -> dict:
    s = settings or get_settings()
    t0 = time.time()
    truth = Truth.load(s)
    wh = s.path("warehouse")
    with duckdb.connect(str(wh), read_only=True, config={"TimeZone": "UTC"}) as con:
        stg = {n: con.execute(f'SELECT * FROM staging."{n}"').df() for n in
               ("pedidos", "itens_pedido", "fretes", "clientes", "enderecos", "produtos", "calendario_comercial")}
        clean = {n: con.execute(f'SELECT * FROM clean."{n}"').df() for n in
                 ("pedidos", "itens_pedido", "fretes", "clientes", "enderecos", "produtos")}
        q = con.execute("SELECT * FROM quarantine.registros").df()
    for name in ("pedidos", "fretes"):
        for c in stg[name].columns:
            if getattr(stg[name][c].dtype, "tz", None) is not None:
                stg[name][c] = stg[name][c].dt.tz_convert("UTC")
    cfg = s.cleaning
    out: dict = {"perfil": s.profile}

    res = {
        "valores_pedido": detection.order_values(stg, truth, cfg),
        "precos_item": detection.item_values(stg, truth, cfg),
        "duplicatas": detection.duplicates_eval(stg, truth, cfg),
        "nao_vendas": detection.not_sales(stg, truth, cfg),
    }
    res["serie_diaria"], series = detection.daily_series(stg, truth, cfg)
    out["deteccao"] = {k: _records(v) for k, v in res.items()}
    out["serie_diaria"] = _records(series)

    acc, side = corrections.correction_accuracy(clean, truth, stg)
    out["correcoes"] = _records(acc)
    out["efeitos_colaterais"] = _records(side)
    out["unificacao_clientes"] = corrections.customer_unification(clean, truth)
    out["quarentena"] = _records(corrections.quarantine_eval(q, truth, int(s.etl["late_window_days"])))
    out["quarentena_valor"] = _records(q.groupby("motivo").agg(linhas=("chave", "size"),
                                                               valor=("valor_centavos", lambda x: float(x.sum()) / 100)).reset_index())
    imp = imputation.compare(stg["fretes"], truth)
    out["imputacao"] = _records(imp)

    con = impact.eval_connection(wh, truth)
    head = impact.headline(con)
    out["metricas"] = json.loads(head.to_json(orient="index", force_ascii=False))
    out["mensal"] = _records(impact.monthly(con))
    out["regional"] = _records(impact.regional(con))
    out["categorias"] = _records(impact.category(con))
    out["decisao_errada"] = impact.wrong_decision(con)
    sc, hist = impact.scaling(con)
    out["escalas"] = _records(sc)
    out["escalas_hist"] = hist
    con.close()

    log = truth.log
    out["registro"] = {
        "defeitos": int((~log["legitimo"]).sum()),
        "legitimos": int(log["legitimo"].sum()),
        "por_tipo": _records(log.groupby(["tipo_defeito", "legitimo"]).size().rename("linhas").reset_index()),
        "pedidos_raw": int(len(stg["pedidos"])),
        "pedidos_gold": int(len(truth.gold["pedidos"])),
        "pedidos_com_defeito": int(log.loc[~log["legitimo"] & log["pedido_ref"].notna() & ~log["tipo_defeito"].str.startswith("formato"),
                                           "pedido_ref"].nunique()),
    }
    out["segundos"] = round(time.time() - t0, 1)

    rep = s.path("reports")
    rep.mkdir(parents=True, exist_ok=True)
    for k, v in res.items():
        v.to_csv(rep / f"deteccao_{k}.csv", index=False)
    acc.to_csv(rep / "correcoes.csv", index=False)
    imp.to_csv(rep / "imputacao.csv", index=False)
    head.to_csv(rep / "metricas_antes_depois.csv")
    sc.to_csv(rep / "escalas.csv", index=False)
    (rep / "evaluation.json").write_text(json.dumps(out, ensure_ascii=False, indent=1, default=_default), encoding="utf-8")
    (rep / "EVALUATION.md").write_text(render(out), encoding="utf-8")
    print(f"avaliação gravada em {rep} ({out['segundos']} s)")
    return out


def _default(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return None if np.isnan(o) else float(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    return str(o)


if __name__ == "__main__":
    main()
