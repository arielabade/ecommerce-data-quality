"""Injeção de defeitos realistas sobre a base gold.

Papel: produzir, a partir do gold (verdade), as tabelas tipadas que o raw vai
conter, com cada defeito registrado no ``DefectLog``.
Entrada: tabelas gold, ``corruption.yaml``, o conjunto de pedidos legítimos
(que nunca recebem defeito, para que cada pedido tenha uma única verdade).
Saída: tabelas tipadas sujas + coluna ``_lote`` (data do lote de ingestão).
Dependências: numpy, pandas.

Regra de exclusividade: cada pedido recebe no máximo um defeito de valor.
Defeitos sistêmicos (fuso do WhatsApp, atraso de lote, formatos) se sobrepõem
aos demais de propósito, porque é assim que aparecem na prática.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from dq.corrupt.baseline import TZ_LOCAL, unaccent
from dq.corrupt.legit import IdSeq, make_order
from dq.corrupt.log import DefectLog
from dq.settings import CONFIG_DIR


def _reais(c) -> str | None:
    return None if pd.isna(c) else f"{int(c) / 100:.2f}"


def _iso(ts) -> str | None:
    return None if pd.isna(ts) else ts.isoformat()


class Injector:
    def __init__(self, gold: dict[str, pd.DataFrame], cfg: dict, legit: set[int], log: DefectLog):
        self.t = {k: v.copy() for k, v in gold.items()}
        self.cfg = cfg
        self.log = log
        self.rng = np.random.default_rng(int(cfg["seed"]))
        self.touched: set[int] = set(legit)
        self.legit = set(legit)
        self.ids = IdSeq(self.t)
        # período real, fixado antes de qualquer defeito de data
        self.t_min, self.t_max = gold["pedidos"]["criado_em"].min(), gold["pedidos"]["criado_em"].max()

    # ------------------------------------------------------------------ util
    def _pick_orders(self, frac: float, mask: pd.Series | None = None, n: int | None = None) -> np.ndarray:
        ped = self.t["pedidos"]
        ok = ~ped["pedido_id"].isin(self.touched)
        if mask is not None:
            ok &= mask
        pool = ped.loc[ok, "pedido_id"].to_numpy()
        k = n if n is not None else int(round(len(ped) * frac))
        k = min(k, len(pool))
        chosen = self.rng.choice(pool, size=k, replace=False) if k else np.array([], dtype=int)
        self.touched |= set(int(x) for x in chosen)
        return np.sort(chosen)

    def _recompute_total(self, idx) -> None:
        p = self.t["pedidos"]
        p.loc[idx, "total_centavos"] = (p.loc[idx, "subtotal_centavos"] - p.loc[idx, "desconto_centavos"]
                                        + p.loc[idx, "frete_centavos"])

    # ------------------------------------------------------------ clientes
    def duplicate_accounts(self) -> None:
        c = self.cfg["clientes"]
        ped, cli = self.t["pedidos"], self.t["clientes"]
        paid = ped[ped["status"].isin(["pago", "enviado", "entregue"]) & ~ped["pedido_id"].isin(self.legit)]
        n_orders = paid.groupby("cliente_id").size()
        eligible = n_orders[n_orders >= 3].index.to_numpy()
        k = int(round(len(cli) * c["conta_duplicada"]))
        chosen = self.rng.choice(eligible, size=min(k, len(eligible)), replace=False)
        new_rows = []
        for orig in np.sort(chosen):
            row = cli.loc[cli["cliente_id"] == orig].iloc[0].to_dict()
            local, dom = row["email"].split("@", 1)
            tag = str(self.rng.choice(["loja", "promo", "tecidos", "2"]))
            email = f"{local}+{tag}@{dom}"
            if self.rng.random() < 0.3:
                email = email.upper()
            orders = ped[(ped["cliente_id"] == orig) & ~ped["pedido_id"].isin(self.legit)].sort_values("criado_em")
            moved = orders.iloc[len(orders) // 2:]
            new_id = int(self.ids.take("clientes")[0])
            row.update(cliente_id=new_id, email=email,
                       cadastrado_em=moved["criado_em"].iloc[0] - pd.Timedelta(minutes=float(self.rng.uniform(2, 20))),
                       origem_cadastro="checkout")
            new_rows.append(row)
            ped.loc[moved.index, "cliente_id"] = new_id
            self.log.add("clientes", new_id, "*", "conta_duplicada", orig, email, detalhe=f"cliente original {orig}")
            self.log.add_many("pedidos", moved["pedido_id"], "cliente_id", "conta_duplicada",
                              [orig] * len(moved), [new_id] * len(moved), pedido_refs=moved["pedido_id"])
        self.t["clientes"] = pd.concat([cli, pd.DataFrame(new_rows)], ignore_index=True)

    def dirty_emails(self) -> None:
        cli = self.t["clientes"]
        logged = set(self.log.frame().query("tabela == 'clientes'")["chave"].astype(int)) if len(self.log.frame()) else set()
        pool = cli.index[~cli["cliente_id"].isin(logged)]
        idx = self.rng.choice(pool, size=int(round(len(cli) * self.cfg["clientes"]["email_sujo"])), replace=False)
        for i in np.sort(idx):
            e = cli.at[i, "email"]
            local, dom = e.split("@", 1)
            kind = self.rng.choice(["upper", "cap_space", "trail", "alias"], p=[0.35, 0.2, 0.2, 0.25])
            dirty = {"upper": e.upper(), "cap_space": " " + e.capitalize(), "trail": e + "  ",
                     "alias": f"{local}+{self.rng.choice(['news', 'compras', 'tecela'])}@{dom}"}[kind]
            cli.at[i, "email"] = dirty
            self.log.add("clientes", cli.at[i, "cliente_id"], "email", "email_sujo", e, dirty, detalhe=str(kind))

    # ------------------------------------------------------------- pedidos
    def freight_absurd(self) -> None:
        p, f = self.t["pedidos"], self.t["fretes"]
        ids = self._pick_orders(self.cfg["pedidos"]["frete_absurdo"], p["frete_centavos"] > 0)
        idx = p.index[p["pedido_id"].isin(ids)]
        fidx = f.set_index("pedido_id")
        for i in idx:
            pid, old = int(p.at[i, "pedido_id"]), int(p.at[i, "frete_centavos"])
            kind = self.rng.choice(["x100", "placeholder", "mult"], p=[0.5, 0.25, 0.25])
            new = {"x100": old * 100, "placeholder": 999999, "mult": int(old * self.rng.uniform(15, 40))}[kind]
            p.at[i, "frete_centavos"] = new
            # metade nasce na cotação (os dois sistemas recebem o valor errado); metade só no export do pedido
            both = self.rng.random() < 0.5
            if both and pid in fidx.index:
                f.loc[f["pedido_id"] == pid, "valor_cobrado_centavos"] = new
            self.log.add("pedidos", pid, "frete", "frete_absurdo", _reais(old), _reais(new), pedido_ref=pid,
                         detalhe=f"{kind}; {'origem' if both else 'export'}")
        self._recompute_total(idx)

    def discount_cents(self) -> None:
        p = self.t["pedidos"]
        ids = self._pick_orders(self.cfg["pedidos"]["desconto_centavos"], p["desconto_centavos"] > 0)
        idx = p.index[p["pedido_id"].isin(ids)]
        old = p.loc[idx, "desconto_centavos"].copy()
        p.loc[idx, "desconto_centavos"] = old * 100
        self._recompute_total(idx)
        self.log.add_many("pedidos", p.loc[idx, "pedido_id"], "desconto", "desconto_maior_que_subtotal",
                          old.map(_reais), p.loc[idx, "desconto_centavos"].map(_reais), pedido_refs=p.loc[idx, "pedido_id"],
                          detalhe="desconto gravado em centavos")

    def freight_weight_swap(self) -> None:
        """Valores normais, combinação absurda: o frete de um pedido leve vai para um pesado e vice-versa."""
        p, f = self.t["pedidos"], self.t["fretes"]
        w = f.set_index("pedido_id")["peso_kg"]
        pw = p["pedido_id"].map(w)
        free = p["frete_centavos"] <= 0
        heavy_mask = (pw >= pw.quantile(0.92)) & ~free
        light_mask = (pw <= pw.quantile(0.10)) & ~free
        n_pairs = int(round(len(p) * self.cfg["pedidos"]["frete_peso_trocado"] / 2))
        heavy = self._pick_orders(0, heavy_mask, n=n_pairs)
        light = self._pick_orders(0, light_mask, n=len(heavy))
        for a, b in zip(heavy, light, strict=True):
            ia, ib = p.index[p["pedido_id"] == a][0], p.index[p["pedido_id"] == b][0]
            fa, fb = int(p.at[ia, "frete_centavos"]), int(p.at[ib, "frete_centavos"])
            p.at[ia, "frete_centavos"], p.at[ib, "frete_centavos"] = fb, fa
            f.loc[f["pedido_id"] == a, "valor_cobrado_centavos"] = fb
            f.loc[f["pedido_id"] == b, "valor_cobrado_centavos"] = fa
            self._recompute_total([ia, ib])
            self.log.add("pedidos", a, "frete", "frete_peso_incoerente", _reais(fa), _reais(fb), pedido_ref=a,
                         detalhe=f"trocado com {b}; peso {w[a]} kg")
            self.log.add("pedidos", b, "frete", "frete_peso_incoerente", _reais(fb), _reais(fa), pedido_ref=b,
                         detalhe=f"trocado com {a}; peso {w[b]} kg")

    def future_dates(self) -> None:
        p = self.t["pedidos"]
        ids = self._pick_orders(self.cfg["pedidos"]["data_futura"])
        idx = p.index[p["pedido_id"].isin(ids)]
        for i in idx:
            old = p.at[i, "criado_em"]
            y = str(old.year)
            new_year = int(y[:2] + y[3] + y[2]) if y[2] != y[3] else old.year + 10  # 2025 -> 2052
            new = old + pd.DateOffset(years=new_year - old.year)  # 29/02 vira 28/02
            p.at[i, "criado_em"] = new
            self.log.add("pedidos", p.at[i, "pedido_id"], "criado_em", "data_futura", _iso(old), _iso(new),
                         pedido_ref=p.at[i, "pedido_id"])

    def whatsapp_utc(self) -> None:
        """Integração do WhatsApp gravou criado_em em UTC sem marcar o fuso (só criado_em)."""
        p = self.t["pedidos"]
        a, b = self.cfg["pedidos"]["fuso_utc_whatsapp"]
        local = p["criado_em"].dt.tz_convert(TZ_LOCAL)
        m = (p["canal_venda"] == "whatsapp") & (local >= pd.Timestamp(a, tz=TZ_LOCAL)) \
            & (local < pd.Timestamp(b, tz=TZ_LOCAL) + pd.Timedelta(days=1)) & ~p["pedido_id"].isin(self.legit)
        m &= ~p["pedido_id"].isin(self.touched)
        idx = p.index[m]
        old = p.loc[idx, "criado_em"].copy()
        # O valor gravado é o relógio UTC lido como se fosse local: no esquema canônico, +3 h.
        p.loc[idx, "criado_em"] = old + pd.Timedelta(hours=3)
        self.log.add_many("pedidos", p.loc[idx, "pedido_id"], "criado_em", "fuso_utc_sem_marcacao",
                          old.map(_iso), p.loc[idx, "criado_em"].map(_iso), pedido_refs=p.loc[idx, "pedido_id"])
        self.tz_shifted = set(int(x) for x in p.loc[idx, "pedido_id"])

    # --------------------------------------------------------------- itens
    def item_defects(self) -> None:
        it = self.t["itens_pedido"]
        c = self.cfg["itens_pedido"]
        n = len(it)
        for kind in ("preco_zero", "preco_negativo", "preco_separador_perdido", "quantidade_negativa"):
            k = int(round(n * c[kind]))
            ok = ~it["pedido_id"].isin(self.touched)
            pool = it.index[ok]
            idx = np.sort(self.rng.choice(pool, size=min(k, len(pool)), replace=False))
            self.touched |= set(int(x) for x in it.loc[idx, "pedido_id"])
            if kind == "quantidade_negativa":
                old = it.loc[idx, "quantidade"].copy()
                it.loc[idx, "quantidade"] = -old
                self.log.add_many("itens_pedido", it.loc[idx, "item_pedido_id"], "quantidade", kind, old,
                                  it.loc[idx, "quantidade"], pedido_refs=it.loc[idx, "pedido_id"])
                continue
            old = it.loc[idx, "preco_unitario_centavos"].copy()
            old_tot = it.loc[idx, "total_item_centavos"].copy()
            if kind == "preco_zero":
                it.loc[idx, "preco_unitario_centavos"] = 0
                it.loc[idx, "total_item_centavos"] = 0
            elif kind == "preco_negativo":
                it.loc[idx, "preco_unitario_centavos"] = -old
                it.loc[idx, "total_item_centavos"] = -old_tot
            else:  # separador decimal perdido no export: "45,90" virou "4590"
                it.loc[idx, "preco_unitario_centavos"] = old * 100
                it.loc[idx, "total_item_centavos"] = old_tot * 100
            self.log.add_many("itens_pedido", it.loc[idx, "item_pedido_id"], "preco_unitario", kind, old.map(_reais),
                              it.loc[idx, "preco_unitario_centavos"].map(_reais), pedido_refs=it.loc[idx, "pedido_id"])

    # -------------------------------------------------------------- fretes
    def delivery_before_purchase(self) -> None:
        f, p = self.t["fretes"], self.t["pedidos"]
        criado = f["pedido_id"].map(p.set_index("pedido_id")["criado_em"])
        mask = f["entregue_em"].notna() & ~f["pedido_id"].isin(self.touched)
        pool = f.index[mask]
        k = int(round(len(f) * self.cfg["fretes"]["entrega_antes_da_compra"]))
        idx = np.sort(self.rng.choice(pool, size=min(k, len(pool)), replace=False))
        for i in idx:
            old = f.at[i, "entregue_em"]
            loc = old.tz_convert(TZ_LOCAL)
            new = None
            if self.rng.random() < 0.4 and loc.day <= 12 and loc.day != loc.month:
                cand = loc.replace(month=loc.day, day=loc.month).tz_convert("UTC")  # dia e mês trocados
                if cand < criado[i]:
                    new = cand
            if new is None:
                new = (loc - pd.DateOffset(years=1)).tz_convert("UTC")
            f.at[i, "entregue_em"] = new
            self.touched.add(int(f.at[i, "pedido_id"]))
            self.log.add("fretes", f.at[i, "frete_id"], "entregue_em", "entrega_antes_da_compra", _iso(old), _iso(new),
                         pedido_ref=f.at[i, "pedido_id"])

    def null_costs(self) -> None:
        f = self.t["fretes"]
        c = self.cfg["fretes"]["nulos_custo_real"]
        base = f["custo_real_centavos"].notna() & (f["transportadora"] != "Retirada na loja")
        taken = pd.Series(False, index=f.index)

        def apply(idx, mech):
            old = f.loc[idx, "custo_real_centavos"].copy()
            f.loc[idx, "custo_real_centavos"] = pd.NA
            taken.loc[idx] = True
            self.log.add_many("fretes", f.loc[idx, "frete_id"], "custo_real", f"nulo_{mech}", old.map(_reais),
                              [None] * len(idx), pedido_refs=f.loc[idx, "pedido_id"], detalhe=mech)

        top = f["custo_real_centavos"] >= f.loc[base, "custo_real_centavos"].quantile(1 - c["mnar_fracao_topo"])
        cand = f.index[base & top]
        apply(cand[self.rng.random(len(cand)) < c["mnar_prob"]], "mnar")
        cand = f.index[base & ~taken & (f["transportadora"] == "Jadlog")]
        apply(cand[self.rng.random(len(cand)) < c["mar_transportadora"]], "mar")
        cand = f.index[base & ~taken]
        apply(cand[self.rng.random(len(cand)) < c["mcar"]], "mcar")

    # ----------------------------------------------------------- enderecos
    APELIDOS = {"São Paulo": ["Sampa", "S. Paulo", "SP"], "Belo Horizonte": ["BH", "Beagá"], "Rio de Janeiro": ["Rio"],
                "Florianópolis": ["Floripa"], "Porto Alegre": ["POA"], "Brasília": ["BSB", "Plano Piloto"],
                "Salvador": ["SSA"], "São José dos Campos": ["SJC", "S. José dos Campos"]}

    def _city_variant(self, city: str, uf: str) -> tuple[str, str]:
        r = self.rng
        kinds = ["upper", "lower", "unaccent", "unaccent_upper", "uf_dash", "uf_slash", "spaces", "typo", "apelido",
                 "accent_wrong"]
        p = np.array([0.18, 0.12, 0.16, 0.08, 0.10, 0.06, 0.08, 0.10, 0.07, 0.05])
        kind = str(r.choice(kinds, p=p / p.sum()))
        if kind == "apelido" and city not in self.APELIDOS:
            kind = "upper"
        if kind == "typo" and len(city) < 6:
            kind = "lower"
        if kind == "accent_wrong" and unaccent(city) == city:
            kind = "unaccent_upper"
        if kind == "upper":
            v = city.upper()
        elif kind == "lower":
            v = city.lower()
        elif kind == "unaccent":
            v = unaccent(city)
        elif kind == "unaccent_upper":
            v = unaccent(city).upper()
        elif kind == "uf_dash":
            v = f"{city} - {uf}"
        elif kind == "uf_slash":
            v = f"{unaccent(city)}/{uf.lower()}"
        elif kind == "spaces":
            v = "  " + city.replace(" ", "  ") + " "
        elif kind == "typo":
            j = int(r.integers(1, len(city) - 2))
            v = city[:j] + city[j + 1:] if r.random() < 0.5 else city[:j] + city[j + 1] + city[j] + city[j + 2:]
        elif kind == "apelido":
            v = str(r.choice(self.APELIDOS[city]))
        else:  # acento no lugar errado: "aracajú", "sao paulô"
            base = unaccent(city).lower()
            j = max(i for i, ch in enumerate(base) if ch in "aeiou")
            v = base[:j] + {"a": "á", "e": "ê", "i": "í", "o": "ô", "u": "ú"}[base[j]] + base[j + 1:]
        return v, kind

    def address_defects(self) -> None:
        e = self.t["enderecos"]
        c = self.cfg["enderecos"]
        n = len(e)
        idx = np.sort(self.rng.choice(e.index, size=int(round(n * c["cidade_variante"])), replace=False))
        for i in idx:
            old = e.at[i, "cidade"]
            v, kind = self._city_variant(old, e.at[i, "uf"])
            if v != old:
                e.at[i, "cidade"] = v
                self.log.add("enderecos", e.at[i, "endereco_id"], "cidade", "cidade_grafia", old, v, detalhe=kind)
        names = self.t["regioes"].set_index("uf")["nome"]
        idx = np.sort(self.rng.choice(e.index, size=int(round(n * c["uf_variante"])), replace=False))
        for i in idx:
            uf = e.at[i, "uf"]
            kind = str(self.rng.choice(["lower", "nome", "nome_sem_acento", "espaco"]))
            v = {"lower": uf.lower(), "nome": names[uf], "nome_sem_acento": unaccent(names[uf]).upper(),
                 "espaco": f" {uf} "}[kind]
            e.at[i, "uf"] = v
            self.log.add("enderecos", e.at[i, "endereco_id"], "uf", "uf_grafia", uf, v, detalhe=kind)
        used = set()
        idx = np.sort(self.rng.choice(e.index, size=int(round(n * c["cep_variante"])), replace=False))
        for i in idx:
            cep = e.at[i, "cep"]
            d = cep.replace("-", "")
            kinds = ["digits", "dots", "space"] + (["zero_lost"] if d.startswith("0") else [])
            kind = str(self.rng.choice(kinds))
            v = {"digits": d, "dots": f"{d[:2]}.{d[2:5]}-{d[5:]}", "space": f" {cep}",
                 "zero_lost": d.lstrip("0")}[kind]
            e.at[i, "cep"] = v
            used.add(i)
            self.log.add("enderecos", e.at[i, "endereco_id"], "cep", "cep_formato", cep, v, detalhe=kind)
        ref = pd.read_csv(CONFIG_DIR / "mappings" / "cidades_referencia.csv")
        ranges = ref.groupby("uf")[["cep5_min", "cep5_max"]].first()
        pool = e.index.difference(list(used))
        idx = np.sort(self.rng.choice(pool, size=int(round(n * c["cep_de_outra_uf"])), replace=False))
        for i in idx:
            uf = e.at[i, "uf"].strip().upper() if len(e.at[i, "uf"].strip()) == 2 else None
            other = str(self.rng.choice([u for u in ranges.index if u != uf]))
            lo, hi = ranges.loc[other]
            v = f"{int(self.rng.integers(lo, hi + 1)):05d}-{int(self.rng.integers(0, 999)):03d}"
            old = e.at[i, "cep"]
            e.at[i, "cep"] = v
            self.log.add("enderecos", e.at[i, "endereco_id"], "cep", "cep_de_outra_uf", old, v, detalhe=other)

    # ------------------------------------------------------------ produtos
    CAT_SINONIMOS = {"Malha PV": ["Malha P.V.", "PV"], "Lycra e Suplex": ["Lycra", "Suplex"],
                     "Sarja e Brim": ["Brim", "Sarja"], "Paetê e Lantejoula": ["Paetês", "Lantejoula"],
                     "Algodão Cru e Morim": ["Morim", "Algodão cru"], "Percal e Cretone": ["Percal", "Cretone"],
                     "Moletom": ["Moleton"], "Jeans": ["Denim"], "Malha 100% Algodão": ["Malha algodão"],
                     "Oxford e Tricoline Mista": ["Oxford"], "Viscose Estampada": ["Viscose estamp."],
                     "Linho Misto": ["Linho/viscose"]}

    def category_text(self) -> None:
        pr = self.t["produtos"]
        idx = np.sort(self.rng.choice(pr.index, size=int(round(len(pr) * self.cfg["produtos"]["categoria_variante"])),
                                      replace=False))
        for i in idx:
            cat = pr.at[i, "categoria"]
            kinds = ["upper", "lower", "spaces", "unaccent", "prefixo", "plural"] + (["sinonimo"] * 3 if cat in self.CAT_SINONIMOS else [])
            kind = str(self.rng.choice(kinds))
            v = {"upper": cat.upper(), "lower": cat.lower(), "spaces": f" {cat}  ".replace(" ", "  ", 1),
                 "unaccent": unaccent(cat), "prefixo": f"Tecido {cat.lower()}", "plural": f"{cat}s",
                 "sinonimo": str(self.rng.choice(self.CAT_SINONIMOS.get(cat, [cat])))}[kind]
            if v != cat:
                pr.at[i, "categoria"] = v
                self.log.add("produtos", pr.at[i, "produto_id"], "categoria", "categoria_grafia", cat, v, detalhe=kind)

    # ----------------------------------------------- registros indevidos
    def _new_customer(self, nome: str, email: str, when: pd.Timestamp, tipo="hobby") -> int:
        cid = int(self.ids.take("clientes")[0])
        row = {"cliente_id": cid, "tipo_cliente": tipo, "tipo_pessoa": "PF", "nome": nome, "razao_social": None,
               "email": email, "cadastrado_em": when, "origem_cadastro": "checkout"}
        self.t["clientes"] = pd.concat([self.t["clientes"], pd.DataFrame([row])], ignore_index=True)
        return cid

    def _add_orders(self, headers, items, fretes) -> None:
        for name, rows in (("pedidos", headers), ("itens_pedido", items), ("fretes", fretes)):
            if rows:
                new = pd.DataFrame(rows).reindex(columns=self.t[name].columns)
                self.t[name] = pd.concat([self.t[name], new], ignore_index=True)

    def test_orders(self) -> None:
        p, var = self.t["pedidos"], self.t["variantes"]
        n = int(round(len(p) * self.cfg["pedidos"]["teste"]))
        start = self.t_min
        staff = [self._new_customer("Teste QA", "qa.teste@tecela.example", start),
                 self._new_customer("Homologação Checkout", "homolog@tecela.example", start),
                 self._new_customer("Equipe Loja TESTE", "loja.teste@tecela.example", start)]
        for cid in staff:
            self.log.add("clientes", cid, "*", "conta_interna_teste")
        metro = var[(var["formato_venda"] == "metro")]["variante_id"].to_numpy()
        span = (self.t_max - start).total_seconds()
        headers, items, fretes = [], [], []
        for _ in range(n):
            when = start + pd.Timedelta(seconds=float(self.rng.uniform(0, span)))
            kind = str(self.rng.choice(["um_real", "um_centavo", "real"], p=[0.5, 0.25, 0.25]))
            v = int(self.rng.choice(metro))
            h, it, f = make_order(self.ids, cliente_id=int(self.rng.choice(staff)), endereco_id=pd.NA,
                                  criado_utc=when, itens=[(v, 1.0)], variantes=var,
                                  status=str(self.rng.choice(["pago", "cancelado"], p=[0.6, 0.4])), canal="site",
                                  ip="10.0.0.12", rng=self.rng, frete_centavos=0,
                                  transportadora="Retirada na loja", servico="Retirada")
            if kind != "real":
                cents = 100 if kind == "um_real" else 1
                it[0].update(preco_unitario_centavos=cents, total_item_centavos=cents)
                h.update(subtotal_centavos=cents, desconto_centavos=0, total_centavos=cents)
            headers.append(h)
            items += it
            fretes.append(f)
            self.log.add("pedidos", h["pedido_id"], "*", "pedido_teste", None, _reais(h["total_centavos"]),
                         pedido_ref=h["pedido_id"], detalhe=kind)
        self._add_orders(headers, items, fretes)
        self.touched |= {h["pedido_id"] for h in headers}

    def bot_orders(self) -> None:
        b = self.cfg["pedidos"]["bots"]
        p, var, cli = self.t["pedidos"], self.t["variantes"], self.t["clientes"]
        cheap = var[(var["formato_venda"] == "metro") & (var["preco_atual_centavos"] < 3000)]["variante_id"].to_numpy()
        # nomes reais do cadastro, sem os das contas de teste da origem (senão o bot vira "teste")
        names = cli.loc[~cli["nome"].str.contains(r"(?i)teste|homolog|\bqa\b", na=False), "nome"].str.split()
        first, last = names.str[0].dropna().unique(), names.str[-1].dropna().unique()
        span_lo, span_hi = p["criado_em"].quantile(0.1), p["criado_em"].quantile(0.95)
        headers, items, fretes = [], [], []
        for burst in range(int(b["rajadas"])):
            t0 = span_lo + (span_hi - span_lo) * float(self.rng.uniform(0, 1))
            minutes = float(self.rng.uniform(*b["minutos"]))
            n = int(self.rng.integers(b["pedidos_por_rajada"][0], b["pedidos_por_rajada"][1] + 1))
            ip = f"45.{self.rng.integers(160, 240)}.{self.rng.integers(1, 255)}.{self.rng.integers(1, 255)}"
            if burst == 0:  # conta real sequestrada para testar cartões
                accounts = [int(self.rng.choice(cli["cliente_id"]))]
            else:
                accounts = []
                for _ in range(int(self.rng.integers(1, 4))):
                    nm = f"{self.rng.choice(first)} {self.rng.choice(last)}"
                    tag = "".join(self.rng.choice(list("abcdefghjkmnpqrstuvwxyz23456789"), size=8))
                    accounts.append(self._new_customer(nm, f"{tag}@email.test", t0 - pd.Timedelta(minutes=5)))
            for _ in range(n):
                when = t0 + pd.Timedelta(seconds=float(self.rng.uniform(0, minutes * 60)))
                st = str(self.rng.choice(["cancelado", "aguardando_pagamento", "pago"], p=[0.85, 0.10, 0.05]))
                h, it, f = make_order(self.ids, cliente_id=int(self.rng.choice(accounts)), endereco_id=pd.NA,
                                      criado_utc=when, itens=[(int(self.rng.choice(cheap)), 1.0)], variantes=var,
                                      status=st, canal="site", ip=ip, rng=self.rng)
                headers.append(h)
                items += it
                fretes.append(f)
                self.log.add("pedidos", h["pedido_id"], "*", "pedido_bot", None, ip, pedido_ref=h["pedido_id"],
                             detalhe=f"rajada {burst}")
        self._add_orders(headers, items, fretes)
        self.touched |= {h["pedido_id"] for h in headers}

    def near_duplicates(self) -> None:
        """Reenvio do checkout: o cliente clica de novo e nasce outro pedido igual segundos depois."""
        p, it, f = self.t["pedidos"], self.t["itens_pedido"], self.t["fretes"]
        ids = self._pick_orders(self.cfg["pedidos"]["duplicata_aproximada"],
                                p["status"].isin(["pago", "enviado", "entregue", "cancelado"]))
        lo, hi = self.cfg["pedidos"]["duplicata_aproximada_segundos"]
        ph = p.set_index("pedido_id")
        headers, items, fretes = [], [], []
        for pid in ids:
            h = ph.loc[pid].to_dict()
            new_id = int(self.ids.take("pedidos")[0])
            dt = pd.Timedelta(seconds=float(self.rng.uniform(lo, hi)))
            h.update(pedido_id=new_id, numero_pedido=str(int(self.ids.take("numero")[0])),
                     criado_em=h["criado_em"] + dt)
            if self.rng.random() < 0.4:  # o gateway recusa o segundo; fica aguardando pagamento
                h.update(status="aguardando_pagamento", pago_em=pd.NaT, cancelado_em=pd.NaT)
            elif pd.notna(h["pago_em"]):
                h["pago_em"] = h["pago_em"] + dt
            headers.append(h)
            for _, r in it[it["pedido_id"] == pid].iterrows():
                r = r.to_dict()
                r.update(item_pedido_id=int(self.ids.take("itens_pedido")[0]), pedido_id=new_id)
                items.append(r)
            fr = f[f["pedido_id"] == pid]
            if len(fr):
                r = fr.iloc[0].to_dict()
                r.update(frete_id=int(self.ids.take("fretes")[0]), pedido_id=new_id)
                fretes.append(r)
            self.log.add("pedidos", new_id, "*", "duplicata_aproximada", pid, new_id, pedido_ref=new_id,
                         detalhe=f"{dt.total_seconds():.0f}s depois de {pid}")
        self._add_orders(headers, items, fretes)
        self.touched |= {h["pedido_id"] for h in headers}

    # --------------------------------------------------------------- lotes
    def assign_batches(self) -> None:
        """``_lote`` = data local em que o registro chegou ao ETL."""
        t = self.t
        for name, col in (("clientes", "cadastrado_em"), ("enderecos", "criado_em")):
            d = t[name][col].dt.tz_convert(TZ_LOCAL).dt.normalize().dt.tz_localize(None)
            t[name]["_lote"] = d.clip(lower=pd.Timestamp("2023-10-01")).dt.strftime("%Y-%m-%d")
        for name in ("regioes", "calendario_comercial", "categorias", "produtos", "variantes"):
            t[name]["_lote"] = "2023-10-01"
        p = t["pedidos"]
        criado = p["criado_em"].copy()
        if hasattr(self, "tz_shifted"):  # o lote é a data real em que o pedido chegou
            m = p["pedido_id"].isin(self.tz_shifted)
            criado[m] = criado[m] - pd.Timedelta(hours=3)
        fut = self.log.frame().query("tipo_defeito == 'data_futura'")
        if len(fut):
            real = pd.to_datetime(fut.set_index("chave")["valor_original"].rename(None), utc=True)
            real.index = real.index.astype(int)
            m = p["pedido_id"].isin(real.index)
            criado[m] = p.loc[m, "pedido_id"].map(real)
        day = criado.dt.tz_convert(TZ_LOCAL).dt.normalize().dt.tz_localize(None)
        p["_lote"] = day.dt.strftime("%Y-%m-%d")
        lote_by_order = p.set_index("pedido_id")["_lote"]
        for name in ("itens_pedido", "fretes"):
            t[name]["_lote"] = t[name]["pedido_id"].map(lote_by_order)

        a = self.cfg["pedidos"]["atraso"]
        pool = p.index[~p["pedido_id"].isin(self.legit)]
        idx = np.sort(self.rng.choice(pool, size=int(round(len(p) * a["fracao"])), replace=False))
        which = self.rng.choice(3, size=len(idx), p=a["pesos"])
        ranges = [a["dias_curto"], a["dias_medio"], a["dias_longo"]]
        lags = np.array([self.rng.integers(ranges[w][0], ranges[w][1] + 1) for w in which])
        new = (pd.to_datetime(p.loc[idx, "_lote"]) + pd.to_timedelta(lags, unit="D")).dt.strftime("%Y-%m-%d")
        old = p.loc[idx, "_lote"].copy()
        p.loc[idx, "_lote"] = new.to_numpy()
        self.log.add_many("pedidos", p.loc[idx, "pedido_id"], "_lote", "chegada_atrasada", old, new,
                          pedido_refs=p.loc[idx, "pedido_id"], detalhes=[f"{int(x)} dias" for x in lags])

    def exact_duplicates(self) -> None:
        t = self.t
        p = t["pedidos"]
        ids = self._pick_orders(self.cfg["pedidos"]["duplicata_exata"])
        for name in ("pedidos", "itens_pedido", "fretes"):
            rows = t[name][t[name]["pedido_id"].isin(ids)]
            t[name] = pd.concat([t[name], rows], ignore_index=True)
        self.log.add_many("pedidos", ids, "*", "duplicata_exata", pedido_refs=ids, detalhe="mesmo lote")
        # reenvio de dias inteiros num lote posterior (replay do ETL)
        c = self.cfg["pedidos"]
        lote = pd.to_datetime(p["_lote"])
        cal = t["calendario_comercial"]
        busy = set(pd.to_datetime(cal.loc[cal["evento_comercial"].notna(), "data"]).dt.strftime("%Y-%m-%d"))
        candidates = sorted(set(p["_lote"]) - busy)
        candidates = [d for d in candidates if lote.quantile(0.2) <= pd.Timestamp(d) <= lote.quantile(0.9)]
        days = sorted(str(x) for x in self.rng.choice(candidates, size=int(c["reenvio_lote_dias"]), replace=False))
        for d in days:
            replay_lote = (pd.Timestamp(d) + pd.Timedelta(days=int(c["reenvio_lote_atraso_dias"]))).strftime("%Y-%m-%d")
            for name in ("pedidos", "itens_pedido", "fretes"):
                rows = t[name][t[name]["_lote"] == d].copy()
                rows["_lote"] = replay_lote
                t[name] = pd.concat([t[name], rows], ignore_index=True)
            pids = t["pedidos"].loc[(t["pedidos"]["_lote"] == replay_lote) & t["pedidos"]["pedido_id"].isin(
                p.loc[p["_lote"] == d, "pedido_id"]), "pedido_id"].unique()
            self.log.add_many("pedidos", pids, "*", "reenvio_de_lote", [d] * len(pids), [replay_lote] * len(pids),
                              pedido_refs=pids,
                              detalhe=f"lote {d} reenviado em {replay_lote}")
            self.log.add("serie_diaria", d, "receita", "reenvio_de_lote", None, None, detalhe=replay_lote)

    # ------------------------------------------------------ integridade
    def orphans(self) -> None:
        t = self.t
        p = t["pedidos"]
        ids = self._pick_orders(self.cfg["pedidos"]["pedido_ausente"])
        lost = p[p["pedido_id"].isin(ids)].drop_duplicates("pedido_id")
        self.log.add_many("pedidos", lost["pedido_id"], "*", "pedido_ausente", lost["total_centavos"].map(_reais),
                          pedido_refs=lost["pedido_id"])
        it = t["itens_pedido"][t["itens_pedido"]["pedido_id"].isin(ids)]
        self.log.add_many("itens_pedido", it["item_pedido_id"], "pedido_id", "item_orfao", it["pedido_id"],
                          it["pedido_id"], pedido_refs=it["pedido_id"])
        fr = t["fretes"][t["fretes"]["pedido_id"].isin(ids)]
        self.log.add_many("fretes", fr["frete_id"], "pedido_id", "frete_orfao", fr["pedido_id"], fr["pedido_id"],
                          pedido_refs=fr["pedido_id"])
        t["pedidos"] = p[~p["pedido_id"].isin(ids)].reset_index(drop=True)

        cli = t["clientes"]
        logged = self.log.frame()
        protected = set(logged.loc[logged["tabela"] == "clientes", "chave"].astype(int))
        owners = p.loc[p["pedido_id"].isin(self.touched), "cliente_id"]
        protected |= set(int(x) for x in owners)
        has_orders = set(p["cliente_id"])
        pool = cli.loc[cli["cliente_id"].isin(has_orders) & ~cli["cliente_id"].isin(protected), "cliente_id"].to_numpy()
        k = int(round(len(cli) * self.cfg["clientes"]["cliente_ausente"]))
        gone = np.sort(self.rng.choice(pool, size=min(k, len(pool)), replace=False))
        self.log.add_many("clientes", gone, "*", "cliente_ausente")
        orf = t["pedidos"][t["pedidos"]["cliente_id"].isin(gone)].drop_duplicates("pedido_id")
        self.log.add_many("pedidos", orf["pedido_id"], "cliente_id", "pedido_orfao_cliente", orf["cliente_id"],
                          orf["cliente_id"], pedido_refs=orf["pedido_id"])
        t["clientes"] = cli[~cli["cliente_id"].isin(gone)].reset_index(drop=True)

    # ----------------------------------------------------------------- run
    def run(self) -> dict[str, pd.DataFrame]:
        self.duplicate_accounts()
        self.dirty_emails()
        self.freight_absurd()
        self.discount_cents()
        self.freight_weight_swap()
        self.future_dates()
        self.whatsapp_utc()
        self.item_defects()
        self.delivery_before_purchase()
        self.null_costs()
        self.address_defects()
        self.category_text()
        self.test_orders()
        self.bot_orders()
        self.near_duplicates()
        self.assign_batches()
        self.exact_duplicates()
        self.orphans()
        return self.t
