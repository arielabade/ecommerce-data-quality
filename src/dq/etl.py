"""``python -m dq.etl``: raw -> staging -> clean -> marts, em incrementos.

Papel: rodar o ETL como ele rodaria em produção: cada execução recebe os
lotes que chegaram até uma data, limpa, valida os contratos, carrega de forma
incremental e reprocessa só os dias afetados. No fim, roda a última execução
de novo e confere que nada mudou (idempotência).
Entrada: ``<paths.raw>`` e ``config/cleaning.yaml``; ``etl.increments`` no
``default.yaml``.
Saída: warehouse DuckDB (``<paths.warehouse>``) e um relatório de qualidade por
execução em ``<paths.runs>/<run_id>/`` (o da última vai também para
``reports/quality_report.md``).
Dependências: ``dq.clean``, ``dq.load``, ``dq.contracts``, ``dq.report.quality``.

Todo o trabalho acontece dentro de ``truth_blocked``: se qualquer linha deste
caminho tentar abrir o registro de defeitos ou a base gold, a execução para.
"""

from __future__ import annotations

import sys
import time

import pandas as pd

from dq import contracts
from dq.clean import pipeline
from dq.clean.staging import local_date
from dq.guard import truth_blocked
from dq.load import integrity, marts, warehouse
from dq.report import quality
from dq.settings import Settings, get_settings

CLEAN_TABLES = ["regioes", "calendario_comercial", "categorias", "produtos", "variantes", "clientes", "enderecos",
                "pedidos", "itens_pedido", "fretes"]


def read_raw(s: Settings, cutoff: str | None) -> dict[str, pd.DataFrame]:
    raw = {}
    for p in sorted(s.path("raw").glob("*.parquet")):
        df = pd.read_parquet(p)
        if cutoff is not None:
            df = df[df["_lote"] <= cutoff]
        raw[p.stem] = df.reset_index(drop=True)
    return raw


def run_once(s: Settings, cutoff: str | None, run_id: str, con) -> dict:
    t0 = time.time()
    raw = read_raw(s, cutoff)
    lote_max = max(df["_lote"].max() for df in raw.values() if len(df))
    rep = {"run_id": run_id, "corte": cutoff or "tudo", "lotes_ate": lote_max,
           "entrada": {k: int(len(v)) for k, v in raw.items()}, "contratos": {}}
    rep["contratos"]["raw"] = contracts.validate("raw", raw, raise_on_fail=True).as_dict()

    res = pipeline.run(raw, s.cleaning)
    rep["contratos"]["staging"] = contracts.validate("staging", res.staging, raise_on_fail=True).as_dict()
    clean, q = integrity.apply_integrity(res.clean, res.quarantine_ids, int(s.etl["late_window_days"]), res.log)
    rep["contratos"]["clean"] = contracts.validate("clean", clean, raise_on_fail=True).as_dict()

    for name, df in res.staging.items():
        df = df.copy()
        if name == "pedidos":
            df["data_local"] = local_date(df["criado_em"])
        warehouse.replace(con, "staging", name, df)
    load, affected = {}, set()
    for name in CLEAN_TABLES:
        r = warehouse.upsert(con, name, clean[name], run_id, lote_max)
        load[name] = {k: v for k, v in r.items() if k not in ("chaves", "removidas")}
        if name == "pedidos":
            ped = warehouse.read(con, "clean", "pedidos")
            touched = r["chaves"] | r["removidas"]
            affected |= set(ped.loc[ped["pedido_id"].astype(str).isin(touched), "data_local"])
            prev = res.staging["pedidos"]
            affected |= set(local_date(prev.loc[prev["pedido_id"].astype(str).isin(r["removidas"]), "criado_em"]).dropna())
        elif name in ("itens_pedido", "fretes") and (r["chaves"] or r["removidas"]):
            ped = warehouse.read(con, "clean", "pedidos")[["pedido_id", "data_local"]]
            df = clean[name]
            pids = set(df.loc[df[warehouse.KEYS[name]].astype(str).isin(r["chaves"]), "pedido_id"])
            affected |= set(ped.loc[ped["pedido_id"].isin(pids), "data_local"])
    rep["carga"] = load
    rep["quarentena"] = warehouse.update_quarantine(con, q, run_id)
    rep["quarentena"]["por_motivo"] = quality.quarantine_by_reason(q)

    first = not marts._exists(con, "marts", "vendas_diarias")
    rep["marts"] = marts.build(con, "clean", "marts", None if first else sorted(affected))
    rep["marts"]["datas_reprocessadas"] = sorted(str(d) for d in affected)[:50] if not first else []
    m = {n: warehouse.read(con, "marts", n) for n in ("vendas_diarias", "metricas_mensais")}
    rep["contratos"]["marts"] = contracts.validate("marts", m, raise_on_fail=True).as_dict()

    rep["tratamento"] = quality.treatment_summary(res.log.frame())
    rep["excluidos"] = quality.excluded_summary(res.excluded, res.staging["pedidos"])
    rep["diagnosticos"] = {"nulos_custo_real": res.diagnostics["nulos_custo_real"],
                           "fuso_segmentos_corrigidos": quality.tz_segments(res.diagnostics["fuso_segmentos"],
                                                                            s.cleaning["datas"])}
    rep["metricas"] = marts.summary(con, "marts").iloc[0].to_dict()
    totals = {k: sum(v[k] for v in load.values()) for k in ("inseridos", "atualizados", "removidos", "inalterados")}
    rep["totais_carga"] = totals
    rep["idempotente"] = totals["inseridos"] == totals["atualizados"] == totals["removidos"] == 0
    rep["segundos"] = round(time.time() - t0, 1)
    con.execute("INSERT INTO meta.execucoes VALUES (?, ?, now(), ?, ?, ?, ?, ?, ?, ?)",
                [run_id, rep["corte"], lote_max, totals["inseridos"], totals["atualizados"], totals["removidos"],
                 totals["inalterados"], rep["quarentena"]["em_quarentena"], rep["idempotente"]])
    res.log.frame().to_parquet(quality.run_dir(s, run_id) / "tratamento.parquet", index=False)
    q.to_parquet(quality.run_dir(s, run_id) / "quarentena.parquet", index=False)
    quality.write(s, rep)
    return rep


def main(argv: list[str] | None = None, settings: Settings | None = None, verbose: bool = True) -> list[dict]:
    s = settings or get_settings()
    argv = argv if argv is not None else sys.argv[1:]
    increments = argv or list(s.etl["increments"])
    reports = []
    with truth_blocked(s.truth_paths()):
        con = warehouse.connect(s.path("warehouse"))
        done = con.execute("SELECT max(lotes_ate) FROM meta.execucoes").fetchone()[0]
        n = con.execute("SELECT count(*) FROM meta.execucoes").fetchone()[0]
        for cut in increments:
            cutoff = None if cut == "all" else cut
            if cutoff is not None and done is not None and pd.Timestamp(cutoff).date() <= done:
                if verbose:
                    print(f"incremento {cut}: lotes já carregados, pulando")
                continue
            n += 1
            rep = run_once(s, cutoff, f"r{n:03d}", con)
            reports.append(rep)
            if verbose:
                _print(rep)
        n += 1
        check = run_once(s, None, f"r{n:03d}", con)
        reports.append(check)
        if verbose:
            _print(check)
        con.close()
    if not check["idempotente"]:
        raise SystemExit("ETL NÃO é idempotente: a reexecução mudou o warehouse")
    if verbose:
        print("idempotência confirmada: a reexecução não inseriu, atualizou nem removeu nada")
    return reports


def _print(rep: dict) -> None:
    t = rep["totais_carga"]
    print(f"[{rep['run_id']}] corte={rep['corte']} lotes até {rep['lotes_ate']}: +{t['inseridos']} ~{t['atualizados']} "
          f"-{t['removidos']} ={t['inalterados']} | quarentena {rep['quarentena']['em_quarentena']} "
          f"(liberados {rep['quarentena']['liberados']}) | marts {rep['marts']['modo']} | {rep['segundos']}s")


if __name__ == "__main__":
    main()
