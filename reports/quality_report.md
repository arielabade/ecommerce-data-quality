# Relatório de qualidade — execução `r003`

Corte: **tudo** · lotes até **2026-10-31** · 57.2 s · idempotente nesta execução: **sim**

## O que entrou

| Tabela | Linhas no raw | Inseridas | Atualizadas | Removidas | Inalteradas |
| --- | ---: | ---: | ---: | ---: | ---: |
| `regioes` | 27 | 0 | 0 | 0 | 27 |
| `calendario_comercial` | 1.109 | 0 | 0 | 0 | 1.109 |
| `categorias` | 29 | 0 | 0 | 0 | 29 |
| `produtos` | 225 | 0 | 0 | 0 | 225 |
| `variantes` | 2.079 | 0 | 0 | 0 | 2.079 |
| `clientes` | 51.647 | 0 | 0 | 0 | 51.035 |
| `enderecos` | 55.684 | 0 | 0 | 0 | 55.514 |
| `pedidos` | 147.654 | 0 | 0 | 0 | 145.073 |
| `itens_pedido` | 275.567 | 0 | 0 | 0 | 270.654 |
| `fretes` | 147.874 | 0 | 0 | 0 | 145.073 |

## Contratos

| Camada | Resultado |
| --- | --- |
| raw | ok (10 tabelas) |
| staging | ok (3 tabelas) |
| clean | ok (6 tabelas) |
| marts | ok (2 tabelas) |

## O que foi tratado

| Tabela | Regra | Ação | Linhas |
| --- | --- | --- | ---: |
| `pedidos` | `conta_mesmo_email` | corrigido | 2.013 |
| `pedidos` | `total_recalculado` | corrigido | 1.971 |
| `itens_pedido` | `preco_separador_decimal` | corrigido | 816 |
| `pedidos` | `fuso_utc_segmento` | corrigido | 614 |
| `fretes` | `entrega_antes_da_compra` | corrigido | 438 |
| `itens_pedido` | `preco_zero_reconstruido_subtotal` | corrigido | 408 |
| `pedidos` | `desconto_em_centavos` | corrigido | 288 |
| `itens_pedido` | `preco_negativo_sinal` | corrigido | 272 |
| `itens_pedido` | `quantidade_negativa_sinal` | corrigido | 272 |
| `pedidos` | `frete_conciliado_transportadora` | corrigido | 223 |
| `pedidos` | `data_futura_ano_do_lote` | corrigido | 146 |
| `fretes` | `custo_real_modelo` | imputado | 19.775 |
| `pedidos` | `frete_implausivel_para_o_peso` | imputado | 1.470 |
| `enderecos` | `cidade_normalizacao` | padronizado | 6.154 |
| `enderecos` | `cep_formato` | padronizado | 5.707 |
| `clientes` | `email_canonico` | padronizado | 2.276 |
| `enderecos` | `uf_mapeamento` | padronizado | 1.259 |
| `enderecos` | `cidade_aproximacao` | padronizado | 812 |
| `enderecos` | `cidade_sinonimo` | padronizado | 235 |
| `produtos` | `categoria_normalizacao` | padronizado | 20 |
| `produtos` | `categoria_sinonimo` | padronizado | 6 |
| `produtos` | `categoria_aproximacao` | padronizado | 2 |
| `pedidos` | `quarentena_pedido` | quarentena | 602 |
| `itens_pedido` | `duplicata_aproximada` | removido | 1.606 |
| `itens_pedido` | `duplicata_exata_hash` | removido | 1.540 |
| `fretes` | `duplicata_aproximada` | removido | 877 |
| `pedidos` | `duplicata_aproximada` | removido | 877 |
| `pedidos` | `duplicata_exata_hash` | removido | 827 |
| `fretes` | `duplicata_exata_hash` | removido | 825 |
| `clientes` | `conta_mesmo_email` | removido | 612 |
| `pedidos` | `bot_rajada_ip` | removido | 157 |
| `pedidos` | `pedido_teste_conta_interna` | removido | 118 |
| `enderecos` | `cep_fora_da_faixa_da_uf` | sinalizado | 278 |
| `pedidos` | `ticket_atipico_para_o_segmento` | sinalizado | 186 |
| `produtos` | `categoria_nao_mapeada` | sinalizado | 3 |

## O que ficou de fora das métricas

### Excluídos (não eram vendas)

| Motivo | Pedidos | Receita paga que inflaria a métrica |
| --- | ---: | ---: |
| bot | 157 | R$ 219,92 |
| duplicata_aproximada | 877 | R$ 119.702,08 |
| pedido_teste | 118 | R$ 618,13 |

### Quarentena (3.087 registros; 0 entraram e 0 foram liberados nesta execução)

| Tabela | Motivo | Linhas | Valor parado |
| --- | --- | ---: | ---: |
| `enderecos` | endereco_sem_cliente | 170 | R$ 0,00 |
| `fretes` | frete_sem_pedido | 222 | R$ 0,00 |
| `fretes` | pai_em_quarentena | 602 | R$ 0,00 |
| `itens_pedido` | item_sem_pedido | 391 | R$ 55.151,75 |
| `itens_pedido` | pai_em_quarentena | 1.100 | R$ 152.380,35 |
| `pedidos` | atraso_fora_da_janela | 270 | R$ 88.911,14 |
| `pedidos` | pedido_sem_cliente | 332 | R$ 71.035,70 |

## Diagnósticos

- **Nulos em `fretes.custo_real`:** 19.775 (13,9%). Por transportadora: Correios 8,2%, Jadlog 33,8%, Loggi 4,6%. Leitura: não é MCAR: a ausência depende da transportadora (MAR) e é maior nos fretes mais pesados/caros (MNAR ou MAR via peso; dados observados não separam os dois).
- **Segmentos com fuso trocado:** whatsapp 2025-03 (96% pagos antes de criados), whatsapp 2025-04 (94% pagos antes de criados), whatsapp 2025-05 (94% pagos antes de criados).
- **Marts:** reprocessamento de 0 dias e 0 meses

## Métricas depois da limpeza

Receita paga R$ 40.311.816,17 · 135.161 pedidos pagos · ticket médio R$ 298,25 · cancelamento 6,83% · recompra 56,5% dos clientes
