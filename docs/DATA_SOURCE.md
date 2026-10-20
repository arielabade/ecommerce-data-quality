# Fonte de dados: Tecelã Tecidos

> Gerado por `make data` (`python -m dq.profile`) a partir da extração da origem.
> Os números abaixo são da escala 1.0 do gerador. **Dados simulados.**

## O que é a origem

A Tecelã Tecidos é uma loja online brasileira de tecidos, **fictícia**, cujo banco
MySQL é gerado pelo repositório [`../ecommerce`](https://github.com/arielabade/dados-sinteticos-ecommerce-tecidos)
(`schema.sql`, `gerador/`, `dbt/`). Ele cobre 01/10/2023 a 13/10/2026 e tem 45 tabelas
(transacional, tracking GA4 server-side, mídia paga, MMM). Este projeto lê **10 delas**:
as que formam pedidos, clientes, catálogo e geografia. Tracking e mídia (mais de 20
milhões de linhas) ficam de fora porque nenhuma métrica aqui depende deles.

| Camada | Tabelas lidas | Grão |
| --- | --- | --- |
| Geografia e calendário | `regioes`, `calendario_comercial` | UF; dia |
| Catálogo | `categorias`, `produtos`, `variantes` | categoria (2 níveis); tecido; SKU (cor x estampa x metro/rolo) |
| Cadastro | `clientes`, `enderecos` | cliente; endereço |
| Transação | `pedidos`, `itens_pedido`, `fretes` | pedido; item; envio (1 por pedido) |

### Convenções da origem que importam para a limpeza

- **Fuso:** tabelas transacionais em horário local (`America/Sao_Paulo`, sem fuso
  gravado); o Brasil não tem horário de verão desde 2019, então local = UTC-3 em
  todo o período. Este projeto guarda tudo em UTC e apresenta em São Paulo.
- **Dinheiro:** `DECIMAL(12,2)` em reais. Aqui vira centavos inteiros.
- **Receita paga:** pedidos com status `pago`, `enviado`, `entregue` ou `devolvido`
  (mesma regra do `stg_pedidos` do dbt da origem).
- **Identidades que valem em 100% das linhas da origem** (e por isso viram regras):
  - `total = subtotal - desconto + frete`: 100,00% das linhas
  - `subtotal = soma(itens.total_item)`: 100,00% das linhas
  - `total_item = quantidade x preco_unitario`: 100,00% das linhas
  - `pedidos.frete = fretes.valor_cobrado`: 100,00% das linhas
  - `desconto <= subtotal`: 100,00% das linhas
  - `preco_unitario` entre 55% e 100% de `preco_tabela`: 100,00% das linhas
  - `entregue_em >= postado_em`: 100,00% das linhas

### Imperfeições que a origem já tem

A origem documenta 24 imperfeições propositais (`../ecommerce/docs/imperfeicoes.md`).
Quatro coincidem com defeitos que este projeto injeta e são **normalizadas antes da
injeção** (`src/dq/corrupt/baseline.py`), para que o registro de defeitos seja a
verdade completa: e-mail com maiúsculas/espaço (~1,5%), CEP sem hífen (~12%), cidade
sem acento ou em maiúsculas (~6%) e 12 pedidos de teste (`pedido_teste = 1`). As
demais (telefone, contas com mesmo CPF, nulos de cadastro, tracking) não entram
porque as colunas não são extraídas.

## Achados do perfil

- **Volume:** 144.571 pedidos reais, 270.242 itens, 51.179 clientes. Média de 121 pedidos pagos por dia (mín 35, máx 467).
- **Status:** 91,3% entregue, 6,8% cancelado, 0,9% devolvido. Taxa de cancelamento é métrica de negócio, não erro.
- **Ticket:** mediana R$ 175,45, p99 R$ 2.088,31, máximo R$ 8.240,35; assimetria 5,3. Cauda longa à direita: compras em rolo de ateliês e confecções.
- **Sazonalidade:** a Black Friday é o maior pico. O dia mais forte (2025-11-28) teve 467 pedidos (3,8x a média) e R$ 106.134 de receita. O limite superior do IQR da receita diária é R$ 64.639: um corte por IQR global já o trata como erro.
- **Pagamento:** 0 pedidos com `pago_em` antes de `criado_em`; mediana de 3,8 min entre criar e pagar. Um lag negativo só pode ser fuso misturado.
- **Frete:** mediana de R$ 20,49 por kg (p99 R$ 117,80); 34,9% dos pedidos com frete zero (frete grátis ou retirada).
- **Recompra:** 55,5% dos clientes com pedido pago compraram 2+ vezes.
- **Fretes:** `entregue_em` nulo em 9,7% (não entregues + ~2% "entregue sem baixa", imperfeição nativa 22); `custo_real` nulo em 0,0%.

## Relacionamentos

| Filho | Pai | Linhas com chave | Órfãs | Cardinalidade |
| --- | --- | ---: | ---: | --- |
| `categorias.categoria_pai_id` | `categorias.categoria_id` | 23 | 0 | N:1 (média 3.83 filhos por pai, máx 6) |
| `produtos.categoria_id` | `categorias.categoria_id` | 225 | 0 | N:1 (média 9.78 filhos por pai, máx 34) |
| `variantes.produto_id` | `produtos.produto_id` | 2.079 | 0 | N:1 (média 9.24 filhos por pai, máx 23) |
| `enderecos.cliente_id` | `clientes.cliente_id` | 55.684 | 0 | N:1 (média 1.09 filhos por pai, máx 2) |
| `enderecos.uf` | `regioes.uf` | 55.684 | 0 | N:1 (média 2062.37 filhos por pai, máx 16045) |
| `pedidos.cliente_id` | `clientes.cliente_id` | 144.583 | 0 | N:1 (média 2.89 filhos por pai, máx 52) |
| `pedidos.endereco_entrega_id` | `enderecos.endereco_id` | 140.719 | 0 | N:1 (média 2.84 filhos por pai, máx 52) |
| `itens_pedido.pedido_id` | `pedidos.pedido_id` | 270.242 | 0 | N:1 (média 1.87 filhos por pai, máx 9) |
| `itens_pedido.variante_id` | `variantes.variante_id` | 270.242 | 0 | N:1 (média 130.68 filhos por pai, máx 2555) |
| `fretes.pedido_id` | `pedidos.pedido_id` | 144.583 | 0 | N:1 (único) |

## Perfil por tabela


### `regioes` — 27 linhas (chave `uf`: 0 duplicadas, 0 linhas idênticas)

| Coluna | Tipo | Nulos | Distintos | Distribuição (mín · mediana · p99 · máx) ou formas | Notas |
| --- | --- | ---: | ---: | --- | --- |
| `uf` | str | 0,0% | 27 | `AA` 100,0% | top: SP (1), RJ (1), MG (1) |
| `nome` | str | 0,0% | 27 | `Aa+` 63,0%, `Aa+ Aa+` 18,5%, `Aáa Aa+` 3,7% | top: São Paulo (1), Rio de Janeiro (1), Minas Gerais (1) |
| `macrorregiao` | str | 0,0% | 5 | `Aa+` 74,1%, `Aa+-Aa+` 14,8%, `Aaa` 11,1% | top: Nordeste (9), Norte (7), Sudeste (4) |
| `sigla_macrorregiao` | str | 0,0% | 5 | `AA` 63,0%, `A` 37,0% | top: NE (9), N (7), SE (4) |

### `calendario_comercial` — 1.109 linhas (chave `data`: 0 duplicadas, 0 linhas idênticas)

| Coluna | Tipo | Nulos | Distintos | Distribuição (mín · mediana · p99 · máx) ou formas | Notas |
| --- | --- | ---: | ---: | --- | --- |
| `data` | str | 0,0% | 1.109 | `9999-99-99` 100,0% | 1 formas |
| `dia_semana` | int64 | 0,0% | 7 | 1,00 · 4,00 · 7,00 · 7,00 | assimetria 0,00; fora do IQR 0,00% |
| `feriado_nacional` | int64 | 0,0% | 2 | 0,00 · 0,00 · 1,00 · 1,00 | assimetria 4,98; fora do IQR 3,61% |
| `nome_feriado` | str | 96,4% | 12 | `Aa+` 45,0%, `Aa+ Aa+` 15,0%, `Aa+ Aa+ Aa+` 10,0% | top: Carnaval (6), Nossa Senhora Aparecida (4), Finados (3) |
| `evento_comercial` | str | 70,2% | 10 | `a+_a+` 41,8%, `a+` 20,9%, `a+_aa_a+` 19,1% | top: festa_junina (105), natal (51), volta_as_aulas (42) |
| `promocao_loja` | int64 | 0,0% | 2 | 0,00 · 0,00 · 1,00 · 1,00 | assimetria 0,97; fora do IQR 0,00% |
| `nome_promocao` | str | 71,9% | 7 | `Aa+ aa Aa+` 37,5%, `Aa+ aa Aa+ a aa Aa+` 26,0%, `Aa+ Aáa-Aa+` 15,4% | top: Semana do Linho e da Viscose (81), Liquidação de Malhas (81), Queima Pós-Natal (48) |

### `categorias` — 29 linhas (chave `categoria_id`: 0 duplicadas, 0 linhas idênticas)

| Coluna | Tipo | Nulos | Distintos | Distribuição (mín · mediana · p99 · máx) ou formas | Notas |
| --- | --- | ---: | ---: | --- | --- |
| `categoria_id` | int64 | 0,0% | 29 | 1,00 · 108,00 · 121,72 · 122,00 | assimetria -1,46; fora do IQR 20,69% |
| `categoria_pai_id` | float64 | 20,7% | 6 | 1,00 · 4,00 · 6,00 · 6,00 | assimetria -0,28; fora do IQR 0,00% |
| `nome` | str | 0,0% | 29 | `Aa+` 51,7%, `Aa+ a Aa+` 20,7%, `Aa+ Aa+` 13,8% | top: Algodão (1), Linho (1), Viscose (1) |
| `slug` | str | 0,0% | 29 | `a+-a+` 24,1%, `a+` 13,8%, `a+-a+-a+` 13,8% | top: algodao (1), linho (1), viscose (1) |

### `produtos` — 225 linhas (chave `produto_id`: 0 duplicadas, 0 linhas idênticas)

| Coluna | Tipo | Nulos | Distintos | Distribuição (mín · mediana · p99 · máx) ou formas | Notas |
| --- | --- | ---: | ---: | --- | --- |
| `produto_id` | int64 | 0,0% | 225 | 1,00 · 113,00 · 222,76 · 225,00 | assimetria 0,00; fora do IQR 0,00% |
| `sku_base` | str | 0,0% | 225 | `A+9999` 100,0% | 1 formas |
| `nome` | str | 0,0% | 220 | `Aa+ Aa+ Aa+ Aa+` 47,6%, `Aa+ Aa+ Aa+` 13,8%, `Aa+ Aa+ Aa+ Aa+ Aa+` 13,8% | 14 formas |
| `categoria_id` | int64 | 0,0% | 23 | 100,00 · 108,00 · 122,00 · 122,00 | assimetria 0,51; fora do IQR 0,00% |
| `fornecedor_id` | int64 | 0,0% | 14 | 1,00 · 8,00 · 14,00 · 14,00 | assimetria -0,04; fora do IQR 0,00% |
| `composicao` | str | 0,0% | 15 | `999% a+` 69,8%, `99% a+ 99% a+` 21,8%, `99% a+ 9% a+` 8,4% | top: 100% algodão (76), 100% viscose (41), 100% poliéster (26) |
| `largura_cm` | int64 | 0,0% | 16 | 110,00 · 150,00 · 250,00 · 260,00 | assimetria 2,11; fora do IQR 10,22% |
| `lancado_em` | str | 0,0% | 197 | `9999-99-99` 100,0% | 1 formas |
| `ativo` | int64 | 0,0% | 2 | 0,00 · 1,00 · 1,00 · 1,00 | assimetria -4,21; fora do IQR 4,89% |

### `variantes` — 2.079 linhas (chave `variante_id`: 0 duplicadas, 0 linhas idênticas)

| Coluna | Tipo | Nulos | Distintos | Distribuição (mín · mediana · p99 · máx) ou formas | Notas |
| --- | --- | ---: | ---: | --- | --- |
| `variante_id` | int64 | 0,0% | 2.079 | 1,00 · 1.040,00 · 2.058,22 · 2.079,00 | assimetria 0,00; fora do IQR 0,00% |
| `produto_id` | int64 | 0,0% | 225 | 1,00 · 116,00 · 224,00 · 225,00 | assimetria -0,07; fora do IQR 0,00% |
| `sku` | str | 0,0% | 2.079 | `A+9999-99999` 83,4%, `A+9999-99999-A` 16,6% | 2 formas |
| `cor` | str | 0,0% | 38 | `Aa+` 60,8%, `Aa+ Aa+` 28,0%, `Aaa-Aa+` 5,3% | top: Colorido (189), Preto (131), Off-White (111) |
| `estampa` | str | 0,0% | 21 | `Aa+` 87,5%, `Aa+ Aa+` 10,6%, `Aaá` 1,2% | top: Liso (1.699), Floral Pequeno (38), Floral Grande (37) |
| `formato_venda` | str | 0,0% | 2 | `a+` 100,0% | top: metro (1.733), rolo (346) |
| `metros_por_unidade` | float64 | 0,0% | 3 | 1,00 · 1,00 · 50,00 · 50,00 | assimetria 1,93; fora do IQR 16,64% |
| `preco_atual` | float64 | 0,0% | 199 | 7,90 · 43,90 · 2.398,82 · 3.186,90 | assimetria 2,52; fora do IQR 18,90% |
| `custo_atual` | float64 | 0,0% | 1.630 | 3,27 · 20,64 · 1.116,42 · 1.526,57 | assimetria 2,50; fora do IQR 18,90% |
| `ativo` | int64 | 0,0% | 2 | 0,00 · 1,00 · 1,00 · 1,00 | assimetria -4,70; fora do IQR 3,99% |
| `criado_em` | str | 0,0% | 39 | `9999-99-99` 100,0% | top: 2023-08-02 (1.716), 2023-08-16 (25), 2023-11-26 (19) |

### `clientes` — 51.179 linhas (chave `cliente_id`: 0 duplicadas, 0 linhas idênticas)

| Coluna | Tipo | Nulos | Distintos | Distribuição (mín · mediana · p99 · máx) ou formas | Notas |
| --- | --- | ---: | ---: | --- | --- |
| `cliente_id` | int64 | 0,0% | 51.179 | 1,00 · 25.590,00 · 50.667,22 · 51.179,00 | assimetria 0,00; fora do IQR 0,00% |
| `tipo_cliente` | str | 0,0% | 4 | `a+` 100,0% | top: hobby (26.415), costureira (17.751), atelie (4.999) |
| `tipo_pessoa` | str | 0,0% | 2 | `AA` 100,0% | top: PF (46.039), PJ (5.140) |
| `nome` | str | 0,0% | 33.636 | `Aa+ Aa+` 42,8%, `Aa+ Aa+ Aa+` 27,4%, `Aaa Aa+ Aa+` 4,4% | 147 formas |
| `razao_social` | str | 90,0% | 4.214 | `Aa+ Aa+ A+` 24,6%, `Aa+ Aa+ Aa+` 13,0%, `Aa+ Aa+ AA` 12,6% | 119 formas |
| `email` | str | 0,0% | 51.179 | `a+@a+.a+` 33,6%, `a+_a+@a+.a+` 15,9%, `a+.a+@a+.a+` 15,8% | 33 formas |
| `cadastrado_em` | str | 0,0% | 51.174 | `9999-99-99 99:99:99.999` 100,0% | 1 formas |
| `origem_cadastro` | str | 0,0% | 4 | `a+` 93,8%, `a+_a+` 6,2% | top: checkout (41.459), newsletter (5.717), importacao_legado (3.200) |

### `enderecos` — 55.684 linhas (chave `endereco_id`: 0 duplicadas, 0 linhas idênticas)

| Coluna | Tipo | Nulos | Distintos | Distribuição (mín · mediana · p99 · máx) ou formas | Notas |
| --- | --- | ---: | ---: | --- | --- |
| `endereco_id` | int64 | 0,0% | 55.684 | 1,00 · 27.842,50 · 55.127,17 · 55.684,00 | assimetria -0,00; fora do IQR 0,00% |
| `cliente_id` | int64 | 0,0% | 51.178 | 1,00 · 25.558,50 · 50.669,17 · 51.178,00 | assimetria 0,00; fora do IQR 0,00% |
| `tipo` | str | 0,0% | 2 | `a+` 100,0% | top: entrega (53.400), cobranca (2.284) |
| `cep` | str | 0,0% | 55.673 | `99999-999` 88,0%, `99999999` 12,1% | 2 formas |
| `bairro` | str | 0,0% | 482 | `Aa+` 32,9%, `Aa+ Aa+` 29,4%, `Aa+ Aa+ Aa+` 5,4% | 44 formas |
| `cidade` | str | 0,0% | 215 | `Aa+` 50,1%, `Aa+ Aa+` 16,0%, `Aáa Aa+` 16,0% | 32 formas |
| `uf` | str | 0,0% | 27 | `AA` 100,0% | top: SP (16.045), RJ (6.595), MG (5.654) |
| `principal` | int64 | 0,0% | 2 | 0,00 · 1,00 · 1,00 · 1,00 | assimetria -3,07; fora do IQR 8,09% |
| `criado_em` | str | 0,0% | 51.173 | `9999-99-99 99:99:99.999` 100,0% | 1 formas |

### `pedidos` — 144.583 linhas (chave `pedido_id`: 0 duplicadas, 0 linhas idênticas)

| Coluna | Tipo | Nulos | Distintos | Distribuição (mín · mediana · p99 · máx) ou formas | Notas |
| --- | --- | ---: | ---: | --- | --- |
| `pedido_id` | int64 | 0,0% | 144.583 | 1,00 · 72.292,00 · 143.137,18 · 144.583,00 | assimetria 0,00; fora do IQR 0,00% |
| `numero_pedido` | int64 | 0,0% | 144.583 | 10.001,00 · 82.292,00 · 153.137,18 · 154.583,00 | assimetria 0,00; fora do IQR 0,00% |
| `cliente_id` | int64 | 0,0% | 50.094 | 1,00 · 21.525,00 · 49.845,18 · 51.179,00 | assimetria 0,22; fora do IQR 0,00% |
| `endereco_entrega_id` | float64 | 2,7% | 49.511 | 1,00 · 23.432,00 · 54.230,82 · 55.684,00 | assimetria 0,21; fora do IQR 0,00% |
| `cupom_id` | float64 | 82,4% | 15 | 1,00 · 2,00 · 15,00 · 15,00 | assimetria 0,40; fora do IQR 0,00% |
| `canal_venda` | str | 0,0% | 2 | `a+` 100,0% | top: site (137.266), whatsapp (7.317) |
| `status` | str | 0,0% | 6 | `a+` 100,0%, `a+_a+` 0,0% | top: entregue (132.006), cancelado (9.898), devolvido (1.231) |
| `criado_em` | str | 0,0% | 144.386 | `9999-99-99 99:99:99.999` 100,0% | 1 formas |
| `pago_em` | str | 6,6% | 135.025 | `9999-99-99 99:99:99.999` 100,0% | 1 formas |
| `cancelado_em` | str | 93,2% | 9.898 | `9999-99-99 99:99:99.999` 100,0% | 1 formas |
| `subtotal` | float64 | 0,0% | 20.931 | 2,45 · 156,95 · 2.162,59 · 8.240,35 | assimetria 5,24; fora do IQR 7,68% |
| `desconto` | float64 | 0,0% | 7.430 | 0,00 · 0,00 · 145,13 · 1.307,06 | assimetria 9,79; fora do IQR 17,25% |
| `frete` | float64 | 0,0% | 7.020 | 0,00 · 17,58 · 75,45 · 767,41 | assimetria 3,29; fora do IQR 1,21% |
| `total` | float64 | 0,0% | 46.868 | 1,00 · 175,45 · 2.088,25 · 8.240,35 | assimetria 5,26; fora do IQR 8,14% |
| `pedido_teste` | int64 | 0,0% | 2 | 0,00 · 0,00 · 0,00 · 1,00 | assimetria 109,75; fora do IQR 0,01% |

### `itens_pedido` — 270.242 linhas (chave `item_pedido_id`: 0 duplicadas, 0 linhas idênticas)

| Coluna | Tipo | Nulos | Distintos | Distribuição (mín · mediana · p99 · máx) ou formas | Notas |
| --- | --- | ---: | ---: | --- | --- |
| `item_pedido_id` | int64 | 0,0% | 270.242 | 1,00 · 135.121,50 · 267.539,59 · 270.242,00 | assimetria 0,00; fora do IQR 0,00% |
| `pedido_id` | int64 | 0,0% | 144.583 | 1,00 · 72.443,50 · 143.159,00 · 144.583,00 | assimetria -0,00; fora do IQR 0,00% |
| `variante_id` | int64 | 0,0% | 2.068 | 1,00 · 932,00 · 2.034,00 · 2.079,00 | assimetria 0,13; fora do IQR 0,00% |
| `quantidade` | float64 | 0,0% | 96 | 0,50 · 2,50 · 14,50 · 66,50 | assimetria 3,41; fora do IQR 6,06% |
| `preco_unitario` | float64 | 0,0% | 773 | 4,90 · 38,90 · 1.135,90 · 3.186,90 | assimetria 9,17; fora do IQR 4,85% |
| `preco_tabela` | float64 | 0,0% | 548 | 6,90 · 39,90 · 1.189,90 · 3.186,90 | assimetria 9,10; fora do IQR 4,99% |
| `custo_unitario` | float64 | 0,0% | 3.980 | 2,71 · 18,22 · 528,82 · 1.394,73 | assimetria 9,10; fora do IQR 5,43% |
| `total_item` | float64 | 0,0% | 4.485 | 2,45 · 89,70 · 1.288,78 · 7.133,70 | assimetria 7,34; fora do IQR 7,80% |

### `fretes` — 144.583 linhas (chave `frete_id`: 0 duplicadas, 0 linhas idênticas)

| Coluna | Tipo | Nulos | Distintos | Distribuição (mín · mediana · p99 · máx) ou formas | Notas |
| --- | --- | ---: | ---: | --- | --- |
| `frete_id` | int64 | 0,0% | 144.583 | 1,00 · 72.292,00 · 143.137,18 · 144.583,00 | assimetria 0,00; fora do IQR 0,00% |
| `pedido_id` | int64 | 0,0% | 144.583 | 1,00 · 72.292,00 · 143.137,18 · 144.583,00 | assimetria 0,00; fora do IQR 0,00% |
| `transportadora` | str | 0,0% | 4 | `Aa+` 97,3%, `Aa+ aa a+` 2,7% | top: Correios (101.100), Jadlog (32.242), Loggi (7.377) |
| `servico` | str | 0,0% | 5 | `A+` 69,9%, `Aa+ .Aa+` 22,3%, `Aa+ Aa+` 5,1% | top: PAC (77.605), Jadlog .Package (32.242), SEDEX (23.495) |
| `peso_kg` | float64 | 0,0% | 10.936 | 0,10 · 1,11 · 17,49 · 89,74 | assimetria 7,57; fora do IQR 8,38% |
| `valor_cobrado` | float64 | 0,0% | 7.020 | 0,00 · 17,58 · 75,45 · 767,41 | assimetria 3,29; fora do IQR 1,21% |
| `custo_real` | float64 | 0,0% | 7.778 | 0,00 · 21,35 · 84,82 · 701,22 | assimetria 5,35; fora do IQR 5,66% |
| `frete_gratis` | int64 | 0,0% | 2 | 0,00 · 0,00 · 1,00 · 1,00 | assimetria 0,63; fora do IQR 0,00% |
| `prazo_estimado_dias` | int64 | 0,0% | 14 | 0,00 · 6,00 · 13,00 · 14,00 | assimetria 0,29; fora do IQR 4,70% |
| `postado_em` | str | 9,5% | 130.070 | `9999-99-99 99:99:99` 100,0% | 1 formas |
| `entregue_em` | str | 9,7% | 130.327 | `9999-99-99 99:99:99` 100,0% | 1 formas |
