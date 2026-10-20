"""Padronização de texto: e-mail, endereço (cidade, UF, CEP) e categoria.

Papel: levar cada valor de texto para a grafia oficial antes de qualquer
agrupamento. "Aracaju", "ARACAJU", "Aracaju - SE" e "aracajú" são a mesma
cidade; sem isso a receita de Aracaju aparece partida em quatro linhas.
Entrada: tabelas de staging ``clientes``, ``enderecos``, ``produtos`` e as
tabelas versionadas em ``config/mappings/``.
Saída: tabelas com texto canônico + linhas no ``TreatmentLog``.
Dependências: rapidfuzz, pandas.

Ordem de tentativa (do mais seguro para o menos seguro):
1. normalização (acento, caixa, espaços, sufixo de UF);
2. tabela de sinônimos versionada (decisão humana registrada);
3. aproximação por similaridade (Damerau-Levenshtein) dentro da mesma UF (erro de digitação);
4. sem correspondência: o valor fica como veio e é sinalizado para revisão.
Nunca se "chuta" fora da UF: um erro de digitação em Santos não pode virar
Santo André do outro lado do estado só porque a similaridade passou.
"""

from __future__ import annotations

import re
import unicodedata

import pandas as pd
from rapidfuzz import fuzz, process
from rapidfuzz.distance import DamerauLevenshtein

from dq.clean.audit import TreatmentLog
from dq.settings import CONFIG_DIR


def norm_key(s: str | None) -> str | None:
    """Chave de comparação: sem acento, minúscula, só letras/dígitos e espaço simples."""
    if s is None or (not isinstance(s, str) and pd.isna(s)):
        return None
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    s = s.replace(".", "")
    s = re.sub(r"[^a-z0-9%]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _mapping(name: str) -> pd.DataFrame:
    return pd.read_csv(CONFIG_DIR / "mappings" / name, dtype=str, keep_default_na=False)


# ----------------------------------------------------------------- e-mail
def canonical_email(s: pd.Series, drop_alias: bool = True) -> pd.Series:
    """Minúsculas, sem espaços e, na chave de identidade, sem ``+alias``.

    ``maria+promo@x.com`` e ``maria@x.com`` entregam na mesma caixa; tratar como
    duas pessoas infla a base de clientes e derruba a taxa de recompra.
    """
    e = s.astype("string").str.strip().str.lower().str.replace(r"\s+", "", regex=True)
    if drop_alias:
        e = e.str.replace(r"\+[^@]*@", "@", regex=True)
    return e


def standardize_emails(cli: pd.DataFrame, log: TreatmentLog, drop_alias: bool = True) -> pd.DataFrame:
    cli = cli.copy()
    new = canonical_email(cli["email"], drop_alias)
    changed = new.ne(cli["email"]).fillna(False)
    log.add_many("clientes", cli.loc[changed, "cliente_id"], "email", "email_canonico", "padronizado",
                 cli.loc[changed, "email"], new[changed])
    cli["email"] = new
    return cli


# ----------------------------------------------------------------- endereço
class AddressStandardizer:
    def __init__(self, fuzzy_min: int = 88):
        ref = pd.read_csv(CONFIG_DIR / "mappings" / "cidades_referencia.csv")
        self.ref = ref
        self.by_uf = {uf: dict(zip(g["cidade"].map(norm_key), g["cidade"], strict=True)) for uf, g in ref.groupby("uf")}
        self.cep_range = ref.groupby("uf")[["cep5_min", "cep5_max"]].first()
        syn = _mapping("cidades_sinonimos.csv")
        self.synonyms = {(r.uf, norm_key(r.variante)): r.cidade for r in syn.itertuples()}
        ufn = _mapping("uf_nomes.csv")
        self.uf_names = {norm_key(r.variante): r.uf for r in ufn.itertuples()}
        self.ufs = set(ref["uf"])
        self.fuzzy_min = fuzzy_min

    def uf(self, v) -> str | None:
        if v is None or pd.isna(v):
            return None
        k = norm_key(v)
        if k and k.upper() in self.ufs:
            return k.upper()
        return self.uf_names.get(k)

    def city(self, v, uf: str | None) -> tuple[str | None, str]:
        """Devolve (cidade canônica, método). Método vazio = não reconhecida."""
        if v is None or pd.isna(v) or uf not in self.by_uf:
            return v, ""
        k = norm_key(v)
        k = re.sub(rf"\s+{uf.lower()}$", "", k)            # "aracaju se", "aracaju/se"
        cands = self.by_uf[uf]
        if k in cands:
            return cands[k], "normalizacao"
        if (uf, k) in self.synonyms:
            return self.synonyms[(uf, k)], "sinonimo"
        # Damerau-Levenshtein conta uma transposição ("Salavdor") como 1 erro, não 2.
        hit = process.extractOne(k, list(cands), scorer=DamerauLevenshtein.normalized_similarity,
                                 score_cutoff=self.fuzzy_min / 100)
        if hit:
            return cands[hit[0]], "aproximacao"
        return v, ""

    @staticmethod
    def cep_digits(v) -> str | None:
        if v is None or pd.isna(v):
            return None
        d = re.sub(r"\D", "", str(v))
        # Planilha come o zero à esquerda: "01310-100" vira "1310100". Só faz
        # sentido restaurar quando faltam exatamente os zeros para chegar a 8.
        return d.zfill(8) if 6 <= len(d) <= 8 else None

    def run(self, end: pd.DataFrame, log: TreatmentLog) -> pd.DataFrame:
        end = end.copy()
        ids = end["endereco_id"]
        new_uf = end["uf"].map(self.uf)
        ch = new_uf.ne(end["uf"]).fillna(True) & new_uf.notna()
        log.add_many("enderecos", ids[ch], "uf", "uf_mapeamento", "padronizado", end.loc[ch, "uf"], new_uf[ch])
        bad_uf = new_uf.isna()
        log.add_many("enderecos", ids[bad_uf], "uf", "uf_desconhecida", "sinalizado", end.loc[bad_uf, "uf"])
        end["uf"] = new_uf

        res = [self.city(c, u) for c, u in zip(end["cidade"], end["uf"], strict=True)]
        new_city = pd.Series([r[0] for r in res], index=end.index, dtype="string")
        method = pd.Series([r[1] for r in res], index=end.index)
        for m in ("normalizacao", "sinonimo", "aproximacao"):
            mk = (method == m) & new_city.ne(end["cidade"]).fillna(False)
            log.add_many("enderecos", ids[mk], "cidade", f"cidade_{m}", "padronizado", end.loc[mk, "cidade"], new_city[mk])
        miss = method == ""
        log.add_many("enderecos", ids[miss], "cidade", "cidade_nao_reconhecida", "sinalizado", end.loc[miss, "cidade"])
        end["cidade"] = new_city

        digits = end["cep"].map(self.cep_digits)
        fmt = digits.map(lambda d: None if d is None else f"{d[:5]}-{d[5:]}")
        ch = fmt.ne(end["cep"]).fillna(False) & fmt.notna()
        log.add_many("enderecos", ids[ch], "cep", "cep_formato", "padronizado", end.loc[ch, "cep"], fmt[ch])
        end["cep"] = fmt.astype("string")
        # CEP fora da faixa da UF: não dá para saber qual dos dois está errado;
        # a UF bate com a cidade, então a UF fica e o CEP é só sinalizado.
        cep5 = pd.to_numeric(end["cep"].str[:5], errors="coerce")
        lo = end["uf"].map(self.cep_range["cep5_min"])
        hi = end["uf"].map(self.cep_range["cep5_max"])
        out = cep5.notna() & lo.notna() & ((cep5 < lo) | (cep5 > hi))
        log.add_many("enderecos", ids[out], "cep", "cep_fora_da_faixa_da_uf", "sinalizado", end.loc[out, "cep"])
        end["cep_inconsistente"] = out
        return end


# ---------------------------------------------------------------- categoria
def standardize_categories(prod: pd.DataFrame, categorias: pd.DataFrame, log: TreatmentLog,
                           fuzzy_min: int = 90) -> pd.DataFrame:
    """Texto livre de categoria -> ``categoria_id`` pela tabela de categorias + sinônimos.

    Valor sem correspondência fica com ``categoria_id`` nulo e vai para a lista
    de revisão: categoria errada contamina a receita por categoria mais do que
    categoria ausente.
    """
    prod = prod.copy()
    subs = categorias[categorias["categoria_pai_id"].notna()]
    canon = {norm_key(n): (int(i), n) for i, n in zip(subs["categoria_id"], subs["nome"], strict=True)}
    syn = {norm_key(r.variante): r.categoria for r in _mapping("categorias_sinonimos.csv").itertuples()}
    by_name = {n: (int(i), n) for i, n in zip(subs["categoria_id"], subs["nome"], strict=True)}

    def match(v) -> tuple[int | None, str | None, str]:
        k = norm_key(v)
        if not k:
            return None, None, ""
        k = re.sub(r"^tecidos? (de )?", "", k)
        for cand in (k, k[:-1] if k.endswith("s") else None):
            if cand and cand in canon:
                return (*canon[cand], "normalizacao")
            if cand and cand in syn:
                return (*by_name[syn[cand]], "sinonimo")
        hit = process.extractOne(k, list(canon), scorer=fuzz.ratio, score_cutoff=fuzzy_min)
        if hit:
            return (*canon[hit[0]], "aproximacao")
        return None, None, ""

    res = [match(v) for v in prod["categoria"]]
    prod["categoria_id"] = pd.array([r[0] for r in res], dtype="Int64")
    new = pd.Series([r[1] for r in res], index=prod.index, dtype="string")
    method = pd.Series([r[2] for r in res], index=prod.index)
    for m in ("normalizacao", "sinonimo", "aproximacao"):
        mk = (method == m) & new.ne(prod["categoria"]).fillna(False)
        log.add_many("produtos", prod.loc[mk, "produto_id"], "categoria", f"categoria_{m}", "padronizado",
                     prod.loc[mk, "categoria"], new[mk])
    miss = method == ""
    log.add_many("produtos", prod.loc[miss, "produto_id"], "categoria", "categoria_nao_mapeada", "sinalizado",
                 prod.loc[miss, "categoria"])
    prod["categoria"] = new.where(~miss, prod["categoria"])
    return prod
