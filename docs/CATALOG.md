# Catálogo de defeitos

Cada tipo de defeito injetado (`src/dq/corrupt`), como ele é detectado e tratado pelo ETL
(`src/dq/clean`, `src/dq/load`) e por que essa foi a decisão. Os nomes entre crases na coluna
**Regra** são os que aparecem no registro de tratamento e no relatório de qualidade de cada
execução. O teste `tests/test_catalog.py` falha se uma regra do código não estiver aqui ou não
tiver teste em `tests/test_rules.py`.

O quanto cada regra acertou está em [`reports/EVALUATION.md`](../reports/EVALUATION.md),
medido contra o registro de defeitos.

**Ações possíveis:** remover · corrigir (valor exato recuperado) · imputar (valor estimado) ·
limitar · anular (valor impossível vira nulo) · sinalizar (fica como está, marcado) ·
quarentena (fora das métricas, guardado para reprocessar) · padronizar (só grafia).

---

## 1. Duplicatas

| Defeito | Como aparece | Regra | Ação | Por quê |
| --- | --- | --- | --- | --- |
| Pedido repetido idêntico (`duplicata_exata`) | Mesma linha 2x no mesmo lote (paginação da API) | `duplicata_exata_hash` | remover | Hash do conteúdo de negócio, ignorando metadados de lote. Cópia exata não tem ambiguidade. |
| Dia inteiro reenviado (`reenvio_de_lote`) | Todo o lote de um dia chega de novo 3 dias depois | `duplicata_exata_hash` | remover | Mesmo hash, lote diferente: fica a cópia do lote mais antigo. |
| Mesma chave com conteúdo diferente | Atualização de status num lote posterior | `chave_repetida_versao_antiga` | remover a versão antiga | Não é duplicata: é a mesma venda atualizada. Vale o lote mais recente. |
| Checkout reenviado (`duplicata_aproximada`) | Novo id e número, mesmo cliente e itens, 3 a 90 s depois | `duplicata_aproximada` | remover | Mesmo cliente + janela de 10 min + Jaccard dos itens ≥ 0,9 + total a 1%. Fica o pedido pago; empate fica o primeiro clique. Só a janela confunde bot e reposição B2B com reenvio. |
| Mesma pessoa em duas contas (`conta_duplicada`) | Segunda conta com `+alias` no e-mail; metade dos pedidos vai para ela | `conta_mesmo_email` | corrigir (pedidos voltam à conta mais antiga) e remover a conta nova | Sem isso a recompra cai: um cliente fiel vira dois clientes de uma compra só. |

## 2. Pedidos que não são vendas

| Defeito | Como aparece | Regra | Ação | Por quê |
| --- | --- | --- | --- | --- |
| Pedido de teste da equipe (`pedido_teste`) | Conta interna (`@tecela.example`, nome "Teste QA"), R$ 1,00 ou R$ 0,01 | `pedido_teste_conta_interna`, `pedido_teste_valor_simbolico` | remover | O menor pedido real da origem custa R$ 4,45; total ≤ R$ 1,00 é teste de gateway. Conta interna é teste em qualquer valor. |
| Pedido de bot (`pedido_bot`) | Rajada de 30 a 60 pedidos do mesmo IP em 8 a 15 min, 85% recusados | `bot_rajada_ip`, `bot_rajada_cliente` | remover | Velocidade: 8+ pedidos do mesmo IP (ou 6+ da mesma conta) em 15 min. Card testing infla pedidos e cancelamento; o maior cliente real faz 2 pedidos por hora. |

## 3. Valores

| Defeito | Como aparece | Regra | Ação | Por quê |
| --- | --- | --- | --- | --- |
| Preço zero (`preco_zero`) | Item com preço e total zerados | `preco_zero_reconstruido_subtotal`; se não fechar, `preco_zero_preco_tabela` | corrigir / imputar | O subtotal do pedido menos os outros itens dá o valor exato do item. Só sem essa redundância usa o preço de tabela. |
| Preço negativo (`preco_negativo`) | Sinal trocado no preço e no total do item | `preco_negativo_sinal` | corrigir | Módulo dentro da faixa da tabela = sinal trocado. |
| Vírgula do preço perdida (`preco_separador_perdido`) | "45,90" exportado como "4590" (x100) | `preco_separador_decimal` | corrigir (÷100) | Preço/tabela entre 50 e 120 só se explica por vírgula perdida (promoção leva a 60% da tabela). |
| Quantidade negativa (`quantidade_negativa`) | Quantidade com sinal trocado, total positivo | `quantidade_negativa_sinal` | corrigir | Total positivo confirma que só o sinal está errado. |
| Total do item incoerente | `total_item ≠ quantidade × preço` depois das correções | `total_item_recalculado` | corrigir | A identidade vale em 100% da origem. |
| Preço fora da faixa da tabela | Razão preço/tabela fora de 55%–100% sem explicação | `preco_fora_da_faixa_tabela` | sinalizar | Pode ser negociação especial; sem segunda fonte, não se inventa preço. |
| Desconto maior que o preço (`desconto_maior_que_subtotal`) | Desconto gravado em centavos (x100) | `desconto_em_centavos`; sem explicação, `desconto_limitado_ao_subtotal` | corrigir / limitar | Desconto ÷100 plausível = unidade errada. Sem isso, limita ao subtotal (desconto não pode passar do preço). |
| Frete absurdo (`frete_absurdo`) | x100, 9.999,99 ou x15–40; metade só no pedido, metade nos dois sistemas | `frete_conciliado_transportadora`, `frete_conciliado_pedido`, `frete_implausivel_para_o_peso` | corrigir / imputar | Primeiro concilia `pedidos.frete` com `fretes.valor_cobrado` (iguais em 100% da origem): vale o que for plausível para peso e UF. Se os dois estão errados, imputa o frete esperado pelo modelo robusto. |
| Frete trocado entre pedidos (`frete_peso_incoerente`) | Frete de um pedido leve vai para um pesado e vice-versa; os dois valores são normais | `frete_implausivel_para_o_peso` | imputar | Só um modelo que olha peso **e** UF vê que aquele frete não cabe naquele pedido. Valor exato é irrecuperável; o modelo chega perto. |
| Total incoerente | `total ≠ subtotal − desconto + frete` depois das correções | `total_recalculado` | corrigir | Componentes corrigidos são mais confiáveis que o total. |
| Subtotal diverge dos itens | Soma dos itens ≠ subtotal depois de tudo | `subtotal_diverge_itens` | sinalizar | Sobrou divergência sem explicação: alguém precisa olhar. |
| Ticket atípico para o segmento | Ticket muito acima dos pares do mesmo tipo de cliente (z robusto > 3,5) | `ticket_atipico_para_o_segmento` | sinalizar | Pedido grande de confecção é o negócio funcionando. O sinal existe para alguém olhar, não para a receita encolher. |

## 4. Datas e fuso

| Defeito | Como aparece | Regra | Ação | Por quê |
| --- | --- | --- | --- | --- |
| Data no futuro (`data_futura`) | Ano com dígitos trocados (2025 → 2052) | `data_futura_ano_do_lote`; sem candidato, `data_futura_irrecuperavel` | corrigir / quarentena | O dia em que o registro chegou (`_lote`) limita a data por cima. Mesmo dia/mês/hora com o ano do lote resolve; se não couber, inventar uma data mudaria a receita de um mês qualquer. |
| Fuso trocado no WhatsApp (`fuso_utc_sem_marcacao`) | Integração gravou `criado_em` em UTC sem marcar, de mar a mai/2025 | `fuso_utc_segmento`; na virada do mês, `fuso_utc_individual` | corrigir (−3 h) | Sinal: pagamento "antes" da compra, que nunca acontece na origem. Segmento canal × mês com 20%+ de lags negativos = fuso trocado, corrige o segmento inteiro (pedidos pagos dias depois também estão deslocados). Lag isolado entre −3 h e 0 tem a mesma assinatura. |
| Pagamento antes da compra sem a assinatura do fuso | Lag negativo maior que 3 h | `pagamento_antes_da_compra` | sinalizar | Não há explicação conhecida; mexer seria chute. |
| Entrega antes da compra (`entrega_antes_da_compra`) | Dia/mês trocados ou ano −1 | `entrega_antes_da_compra` | corrigir; sem candidato, anular | Aceita o candidato que cai entre a postagem e 90 dias depois. Sem ele, a data vira nula: o pedido sai do prazo médio de entrega mas continua na receita. |
| Data em UTC explícito / dd/mm/aaaa (`formato_data_*`) | "2024-05-03T16:22:01Z", "03/05/2024 13:22:01" | (staging) | padronizar | O parser entende os três formatos; ler o "Z" como hora local erraria 3 h sem aviso. Tudo é guardado em UTC e apresentado em America/Sao_Paulo. |

## 5. Texto e cadastro

| Defeito | Como aparece | Regra | Ação | Por quê |
| --- | --- | --- | --- | --- |
| E-mail sujo (`email_sujo`) | Maiúsculas, espaços, `+alias` | `email_canonico` | padronizar | Minúsculas, sem espaço, sem `+alias` na chave de identidade. `maria+promo@x` entrega na mesma caixa que `maria@x`. |
| Cidade com grafia diferente (`cidade_grafia`) | "ARACAJU", "aracajú", "Aracaju - SE", "Salavdor", "Sampa" | `cidade_normalizacao`, `cidade_sinonimo`, `cidade_aproximacao` | padronizar | Nesta ordem: normalização (acento, caixa, sufixo de UF); tabela de sinônimos versionada (`config/mappings/cidades_sinonimos.csv`); similaridade Damerau-Levenshtein ≥ 80 **dentro da UF**. |
| Cidade não reconhecida | Nada acima casa | `cidade_nao_reconhecida` | sinalizar | Fica como veio e entra na lista de revisão. Nunca se chuta fora da UF. |
| UF com grafia diferente (`uf_grafia`) | "se", "Sergipe", "SAO PAULO" | `uf_mapeamento`; inválida, `uf_desconhecida` | padronizar / sinalizar | Tabela de nomes de UF versionada. Sem isso, a receita cai em "Sem região" e o Sudeste encolhe. |
| CEP em outro formato (`cep_formato`) | Sem hífen, com pontos, sem o zero à esquerda (planilha) | `cep_formato` | padronizar | Só dígitos, zero à esquerda restaurado quando faltam até 2 dígitos, formato 00000-000. |
| CEP de outra UF (`cep_de_outra_uf`) | CEP fora da faixa da UF | `cep_fora_da_faixa_da_uf` | sinalizar | Não dá para saber qual dos dois está errado; a UF bate com a cidade, então fica a UF. |
| Categoria com grafia diferente (`categoria_grafia`) | "TRICOLINE", "Tecido tricoline", "Tricolines", "PV", "Denim" | `categoria_normalizacao`, `categoria_sinonimo`, `categoria_aproximacao` | padronizar | Normalização + tabela de sinônimos versionada + similaridade ≥ 90. |
| Categoria não mapeada | Sinônimo que a tabela ainda não conhece ("Denim") | `categoria_nao_mapeada` | sinalizar | Categoria errada contamina a receita por categoria mais do que categoria ausente. Entra na lista de revisão e na próxima versão da tabela. |

## 6. Nulos

| Defeito | Como aparece | Regra | Ação | Por quê |
| --- | --- | --- | --- | --- |
| Custo de frete ausente, ao acaso (`nulo_mcar`) | 4% aleatórios | `custo_real_modelo` (ou `custo_real_mediana_grupo`) | imputar e marcar `custo_real_imputado` | Antes de imputar, o diagnóstico (qui-quadrado por transportadora, KS do peso) diz que a ausência não é aleatória. |
| Ausente por transportadora (`nulo_mar`) | Jadlog não devolve o custo em 30% dos envios | idem | imputar | Excluir enviesaria (a Jadlog tem custo diferente); o modelo condiciona na transportadora. |
| Ausente nos mais caros (`nulo_mnar`) | 35% dos 10% fretes mais caros somem | idem | imputar | Nenhum método com dados observados corrige por inteiro; peso e valor cobrado reduzem o viés. O documento de técnicas mostra quanto sobra. |

## 7. Integridade e chegada

| Defeito | Como aparece | Regra / motivo de quarentena | Ação | Por quê |
| --- | --- | --- | --- | --- |
| Pedido perdido (`pedido_ausente`) → item sem pedido (`item_orfao`), frete sem pedido (`frete_orfao`) | Cabeçalho do pedido nunca chega | `item_sem_pedido`, `frete_sem_pedido` | quarentena | Pode ser só atraso do cabeçalho. A cada execução é testado de novo; quando o pai chega, entra. |
| Pedido sem cliente (`pedido_orfao_cliente`) | Cadastro do cliente perdido (`cliente_ausente`) | `pedido_sem_cliente` | quarentena | Venda real, mas sem cliente não entra em recompra nem em região. O valor parado aparece no relatório. |
| Endereço sem cliente | Mesmo caso, no endereço | `endereco_sem_cliente` | quarentena | Idem. |
| Filhos de pedido em quarentena | Itens e frete de um pedido que foi para a quarentena | `pai_em_quarentena` | quarentena | Item sem cabeçalho na métrica é receita sem pedido. |
| Chegada atrasada (`chegada_atrasada`) | Pedido chega 1 a 25 dias depois da compra | `atraso_fora_da_janela` (só acima de 7 dias) | carregar e reprocessar o dia / quarentena | Até 7 dias: entra e o dia é recalculado nos marts. Depois disso o mês já foi fechado e reportado: vai para quarentena e só entra reabrindo o período. |
| Data irrecuperável | Data futura sem candidato plausível | `data_irrecuperavel` | quarentena | Ver seção 4. |
| Qualquer pedido que vai para a quarentena | — | `quarentena_pedido` | quarentena | Registro único no log de tratamento, para auditoria. |

## 8. Formato (sem mudança de valor)

| Defeito | Como aparece | Tratamento | Por quê |
| --- | --- | --- | --- |
| Moeda em formato brasileiro (`formato_moeda_br`) | "1.234,56" | staging: com vírgula, a vírgula é o decimal e o ponto é milhar | Um export não mistura os dois papéis do ponto na mesma célula. Valores viram centavos inteiros (soma de float acumula erro). |

## 9. Outliers legítimos (não são defeito)

| Evento | Como aparece | O que o ETL faz | Por quê |
| --- | --- | --- | --- |
| Black Friday (`legitimo_black_friday`) | Pico de pedidos e receita nos dias da promoção (já existe na origem) | nada; o monitor STL + calendário registra como esperado | É o melhor dia do ano, não erro. |
| Cliente B2B (`legitimo_b2b`) | Confecção que compra rolos: pedidos de R$ 4 mil a R$ 118 mil | `ticket_atipico_para_o_segmento` só se for atípico **para confecção** | Comparar com pares do mesmo segmento. |
| Produto viral (`legitimo_viral`) | Uma chita vende +90% de pedidos por 10 dias em junho/2025 | nada | Alerta sem evento comercial vira investigação, não exclusão. |
