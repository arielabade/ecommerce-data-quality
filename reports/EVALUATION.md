# Avaliação da limpeza

Perfil `default`. Gerado por `make evaluate`. Dados simulados.

## Detecção: Valores de pedido (frete, desconto, total)

| Técnica | Sinalizados | Acertos | Erros | Precisão | Recall | Legítimos derrubados | Receita legítima derrubada (R$) | Distorção que passou (R$) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| IQR global | 34.697 | 942 | 1.022 | 2,7% | 92,2% | 2.499 | 2.578.530 | 3.156 |
| z-score (média/desvio) | 908 | 542 | 1.022 | 59,7% | 53,0% | 41 | 1.635.001 | 121.903 |
| z robusto (MAD) global | 13.223 | 893 | 1.022 | 6,8% | 87,4% | 891 | 2.300.913 | 5.810 |
| z robusto (MAD) por grupo | 47.606 | 749 | 1.022 | 1,6% | 73,3% | 2.107 | 2.572.707 | 18.639 |
| Isolation Forest | 1.456 | 560 | 1.022 | 38,5% | 54,8% | 142 | 1.747.021 | 230.493 |
| Regras de negócio | 512 | 512 | 1.022 | 100,0% | 50,1% | 0 | 0 | 814.835 |
| Modelo frete x peso x UF (Huber + MAD) | 1.693 | 733 | 1.022 | 43,3% | 71,7% | 45 | 63.165 | 909.494 |
| Regras + modelo de frete (usado no ETL) | 1.971 | 1.011 | 1.022 | 51,3% | 98,9% | 45 | 63.165 | 2.075 |

## Detecção: Preços e quantidades de item

| Técnica | Sinalizados | Acertos | Erros | Precisão | Recall | Legítimos derrubados |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| IQR global (preço e quantidade) | 30.974 | 1.637 | 1.768 | 5,3% | 92,6% | 127 |
| z-score (média/desvio) | 6.130 | 672 | 1.768 | 11,0% | 38,0% | 53 |
| z robusto (MAD) global | 30.219 | 1.220 | 1.768 | 4,0% | 69,0% | 123 |
| z robusto (MAD) por variante | 16.487 | 1.441 | 1.768 | 8,7% | 81,5% | 42 |
| Isolation Forest | 2.694 | 1.495 | 1.768 | 55,5% | 84,6% | 95 |
| Regras de negócio (usado no ETL) | 1.768 | 1.768 | 1.768 | 100,0% | 100,0% | 0 |

## Detecção: Série diária de receita

| Técnica | Sinalizados | Acertos | Erros | Precisão | Recall | Legítimos derrubados |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| IQR global | 28 | 0 | 2 | 0,0% | 0,0% | 2 |
| z-score (média/desvio) | 19 | 0 | 2 | 0,0% | 0,0% | 1 |
| z robusto (MAD) global | 21 | 0 | 2 | 0,0% | 0,0% | 2 |
| STL (resíduo) | 52 | 1 | 2 | 1,9% | 50,0% | 5 |
| STL + calendário comercial | 28 | 1 | 2 | 3,6% | 50,0% | 0 |

## Detecção: Duplicatas

| Técnica | Sinalizados | Acertos | Erros | Precisão | Recall | Legítimos derrubados |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Hash exato do conteúdo | 827 | 827 | 829 | 100,0% | 99,8% | 0 |
| Janela de 10 min, sem olhar itens | 1.027 | 877 | 877 | 85,4% | 100,0% | 0 |
| Janela de 10 min + itens iguais (usado no ETL) | 884 | 877 | 877 | 99,2% | 100,0% | 0 |
| Janela de 2 h + itens iguais | 884 | 877 | 877 | 99,2% | 100,0% | 0 |

## Detecção: Pedidos de teste e de bot

| Técnica | Sinalizados | Acertos | Erros | Precisão | Recall | Legítimos derrubados |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Regra de pedido de teste | 118 | 117 | 117 | 99,2% | 100,0% | 1 |
| Velocidade por IP | 157 | 157 | 157 | 100,0% | 100,0% | 0 |
| Velocidade por cliente | 157 | 157 | 157 | 100,0% | 100,0% | 0 |
| IP ou cliente (usado no ETL) | 157 | 157 | 157 | 100,0% | 100,0% | 0 |

## Correções: o valor limpo bate com a verdade?

| Defeito | Registros | Restaurados exatos | Valor diferente | Fora do clean | Taxa | Erro médio antes | Erro médio depois |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| cidade_grafia | 7.572 | 7.541 | 0 | 31 | 99,6% | — | — |
| uf_grafia | 1.671 | 1.664 | 0 | 7 | 99,6% | — | — |
| cep_formato | 8.353 | 8.326 | 0 | 27 | 99,7% | — | — |
| categoria_grafia | 46 | 43 | 3 | 0 | 93,5% | — | — |
| email_sujo | 2.072 | 2.072 | 0 | 0 | 100,0% | — | — |
| frete_absurdo | 438 | 224 | 214 | 0 | 51,1% | 3.810,80 | 0,59 |
| frete_peso_incoerente | 292 | 0 | 292 | 0 | 0,0% | 69,87 | 14,49 |
| desconto_maior_que_subtotal | 292 | 288 | 4 | 0 | 98,6% | 3.768,65 | 6,78 |
| data_futura | 146 | 145 | 1 | 0 | 99,3% | 227,49 | 0,00 |
| fuso_utc_sem_marcacao | 605 | 603 | 0 | 2 | 99,7% | 0,00 | 0,00 |
| entrega_antes_da_compra | 438 | 437 | 1 | 0 | 99,8% | 8,19 | 0,00 |
| preco_zero | 408 | 407 | 0 | 1 | 99,8% | 66,21 | 0,00 |
| preco_negativo | 272 | 271 | 0 | 1 | 99,6% | 96,49 | 0,00 |
| preco_separador_perdido | 816 | 813 | 0 | 3 | 99,6% | 5.609,48 | 0,00 |
| quantidade_negativa | 272 | 271 | 0 | 1 | 99,6% | 5,90 | 0,00 |
| formato_moeda_br | 16.947 | 16.801 | 111 | 35 | 99,1% | 0,00 | 0,39 |
| formato_data_iso_utc | 8.520 | 8.506 | 1 | 13 | 99,8% | 0,00 | 0,00 |
| formato_data_br | 7.106 | 7.101 | 0 | 5 | 99,9% | 0,00 | 0,00 |

Erro médio em R$ (valores), horas (datas) ou unidades (quantidade), contra o gold.

Efeito colateral (valores sem defeito alterados pela limpeza):

| Tabela | Coluna | Sem defeito | Alterados indevidamente |
| --- | ---: | ---: | ---: |
| enderecos | cidade | 39.313 | 0 |
| enderecos | uf | 39.313 | 0 |
| produtos | categoria | 179 | 0 |
| clientes | email | 48.952 | 0 |

Unificação de clientes: {'contas_duplicadas': 614, 'contas_restantes_no_clean': 2, 'pedidos_reatribuidos': 2008, 'pedidos_de_volta_ao_dono': 1992, 'taxa': 0.9964982491245623}

## Quarentena

| Situação | Registros | Em quarentena | Recall |
| --- | ---: | ---: | ---: |
| item_orfao | 391 | 391 | 100,0% |
| frete_orfao | 222 | 222 | 100,0% |
| pedido_orfao_cliente | 332 | 332 | 100,0% |
| atraso dentro da janela | 5.609 | 0 | 0,0% |
| atraso fora da janela | 273 | 270 | 98,9% |
| quarentena por atraso sem atraso real | 0 | 0 | — |

## Nulos: comparação de métodos (`fretes.custo_real`)

| Método | Mecanismo | Linhas | Erro abs. médio (R$) | Viés |
| --- | ---: | ---: | ---: | ---: |
| exclusao | MCAR | 5.121 | — | — |
| exclusao | MAR | 9.736 | — | — |
| exclusao | MNAR | 4.918 | — | — |
| exclusao | todos | 19.775 | — | — |
| exclusao | custo médio por envio | 143.054 | -1,04 | -0,039 |
| mediana_grupo | MCAR | 5.121 | 5,55 | -1,078 |
| mediana_grupo | MAR | 9.736 | 5,08 | -1,173 |
| mediana_grupo | MNAR | 4.918 | 21,93 | -4,396 |
| mediana_grupo | todos | 19.775 | 9,39 | -1,950 |
| mediana_grupo | custo médio por envio | 143.054 | -0,29 | -0,011 |
| modelo | MCAR | 5.121 | 2,60 | -0,270 |
| modelo | MAR | 9.736 | 2,75 | -0,441 |
| modelo | MNAR | 4.918 | 9,96 | -4,999 |
| modelo | todos | 19.775 | 4,50 | -1,530 |
| modelo | custo médio por envio | 143.054 | -0,23 | -0,009 |

## Métricas: verdade x antes x depois

| Métrica | Verdade | Antes | Depois |
| --- | ---: | ---: | ---: |
| Receita paga (R$) | 40.556.462 | 41.338.382 | 40.311.816 |
| Pedidos | 145.898 | 147.654 | 145.073 |
| Pedidos pagos | 135.931 | 137.002 | 135.161 |
| Ticket médio (R$) | 298,36 | 301,74 | 298,25 |
| Cancelamento | 6,83% | 6,94% | 6,83% |
| Recompra (clientes 2+) | 56,5% | 57,1% | 56,5% |
| Pedidos de recompra | 64,1% | 64,0% | 64,1% |
| Clientes compradores | 48.756 | 49.343 | 48.544 |
| Categorias distintas | 23 | 47 | 25 |

## Decisão errada: IQR global

- **Remover pedidos acima da cerca do IQR:** mês mais afetado 2025-05 (-47,5%); novembros: 2023-11 -30,9%, 2024-11 -34,7%, 2025-11 -37,8%; média dos outros meses -38,4%.
- **Limitar a receita diária na cerca do IQR:** mês mais afetado 2025-05 (-12,8%); novembros: 2023-11 0,0%, 2024-11 0,0%, 2025-11 -5,1%; média dos outros meses -2,0%.
- **Apagar os dias acima da cerca do IQR:** mês mais afetado 2025-05 (-22,9%); novembros: 2023-11 0,0%, 2024-11 0,0%, 2025-11 -14,5%; média dos outros meses -5,8%.
- Evento legítimo **b2b** (R$ 1.600.059): removido 100,0% apagando pedidos pelo IQR; 90,7% apagando dias pelo IQR.
- Evento legítimo **black_friday** (R$ 1.654.164): removido 30,1% apagando pedidos pelo IQR; 11,2% apagando dias pelo IQR.
- Evento legítimo **viral** (R$ 122.573): removido 0,0% apagando pedidos pelo IQR; 0,0% apagando dias pelo IQR.

## Escalas para modelagem (ticket por pedido)

| Escala | Assimetria | % no 1º décimo da faixa | % com |z robusto| > 3,5 |
| --- | ---: | ---: | ---: |
| original (R$) | 66,58 | 100,0% | 7,9% |
| min-max | 66,58 | 100,0% | 7,9% |
| z-score | 66,58 | 100,0% | 7,9% |
| escala robusta (mediana/IQR) | 66,58 | 100,0% | 7,9% |
| log(1 + x) | 0,45 | 0,0% | 0,2% |
| Yeo-Johnson | 0,00 | 0,0% | 0,1% |
