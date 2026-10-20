# Decisões

Cada decisão com as alternativas consideradas e o motivo da escolha. Onde havia duas opções
razoáveis, ficou a recomendada e o registro está aqui.

## Dados e experimento

**D1. Ler 10 das 45 tabelas da origem.** Alternativas: incluir tracking (sessões, eventos) e
mídia. Motivo: nenhuma métrica pedida (receita, ticket, pedidos, recompra, cancelamento por mês
e região) depende delas, e eventos sozinhos têm 20 milhões de linhas. Ficam de fora sem perda.

**D2. Normalizar as imperfeições nativas da origem antes de injetar defeitos.** A origem já
traz e-mail em maiúsculas, CEP sem hífen, cidade sem acento e 12 pedidos de teste. Alternativa:
deixar e contar como "defeito não registrado". Motivo: se ficassem, uma limpeza correta seria
punida como falso positivo e o registro de defeitos deixaria de ser a verdade completa.

**D3. Outliers legítimos entram no gold, com semente própria.** A Black Friday já existe na
origem; o cliente B2B e o produto viral são acrescentados. A semente deles é diferente da
semente da sujeira: trocar a sujeira não muda a verdade (há teste). Os pedidos novos usam a
tarifa real da transportadora (frete de um pedido da origem com mesma UF, serviço e peso
próximo); com frete sorteado ao acaso, eles seriam "outliers" por construção e a avaliação
acusaria falsos positivos que não existem.

**D4. Cada pedido recebe no máximo um defeito de valor; defeitos sistêmicos se sobrepõem.**
Alternativa: sortear tudo independente. Motivo: com vários defeitos de valor no mesmo pedido,
não dá para atribuir o erro restante a uma regra. Fuso, atraso de lote e formato se sobrepõem
de propósito, porque é assim que acontecem.

**D5. IP de checkout sintético.** A origem não tem IP. Alternativa: detectar bots só pela
conta. Motivo: bot real troca de conta e mantém a origem; cada cliente ganhou um IP "de casa"
(80% dos pedidos) e o resto sai de rede móvel, de forma determinística.

**D6. Extração por MySQL como padrão, com CSV como alternativa equivalente.** Durante a
construção, o MySQL de origem estava sendo recarregado por outro processo (`make banco` em
`../ecommerce`) e a porta só abre quando a carga inteira termina. A extração foi feita dos CSVs
que o banco carrega (`DQ_SOURCE_KIND=csv`) e conferida contra o MySQL por `docker exec`:
mesmas contagens e mesmas somas de controle (total, frete, custo, peso) nas 8 tabelas
transacionais. Nada no repositório de origem foi alterado.

## Arquitetura

**D7. DuckDB como warehouse, Parquet como raw.** Alternativas: Postgres, SQLite, dbt.
Motivo: DuckDB roda sem servidor no CI, lê pandas sem cópia, tem `INSERT ... BY NAME`, fuso
horário (ICU) e é rápido o bastante para 670 mil linhas por execução. dbt não foi usado: os
marts são 5 consultas, e os testes de contrato já estão em pandera em todas as camadas
(um segundo framework de teste só para os marts não pagava a dependência).

**D8. Clean recalculado sobre tudo que chegou; carga incremental por chave + hash.**
Alternativa: limpar só o lote novo. Motivo: duplicatas, rajadas de bot e fuso por segmento
atravessam lotes; limpar lote a lote perderia exatamente esses defeitos. Nesta escala recalcular
custa ~50 s. A carga, que é o que mexe no warehouse, é incremental e idempotente.

**D9. Colunas derivadas voláteis fora da tabela clean.** O z robusto de cada pedido mudava um
pouco a cada dado novo e fazia toda carga incremental atualizar 150 mil linhas. Só a flag
(`outlier_ticket`) é gravada. O custo de frete imputado continua sendo recalibrado (é valor de
negócio), e por isso o segundo incremento atualiza ~20 mil linhas: é reprocessamento esperado,
não falta de idempotência (com a mesma entrada, zero mudanças).

**D10. Janela de reprocessamento de 7 dias, depois quarentena.** Alternativas: aceitar qualquer
atraso (reabre meses fechados sem aviso) ou descartar (perde receita real). Motivo: até 7 dias o
dia é recalculado; depois disso o valor fica parado na quarentena com o montante em reais no
relatório, e só entra por decisão explícita.

**D11. Quarentena em vez de exclusão para órfãos.** Item sem pedido muitas vezes é cabeçalho
atrasado. A cada execução a quarentena é refeita e o histórico registra quando cada registro
entrou e saiu.

**D12. O gabarito é bloqueado em tempo de execução, não só por convenção.** `sys.addaudithook`
intercepta a abertura de qualquer arquivo sob `data/defect_log` ou `data/gold` enquanto o ETL
roda. Há também a busca estática no código e um teste com um ETL "trapaceiro".

## Limpeza

**D13. Regras de negócio antes de estatística.** A origem respeita várias identidades em 100%
das linhas (ver `DATA_SOURCE.md`). Uma regra com segunda fonte recupera o valor exato; estatística
só aponta suspeitos. Por isso a ordem: conciliar, depois modelar, depois sinalizar.

**D14. MAD e não desvio padrão.** O desvio padrão é inflado pelos próprios erros (um frete de
R$ 9.999,99 sozinho dobra o desvio de milhares de linhas). A mediana dos desvios absolutos
ignora até 50% de lixo. Corte em 3,5 (Iglewicz & Hoaglin).

**D15. Modelo de frete com UF.** Sem a UF, o resíduo típico do frete é 25% e um frete trocado
entre pedidos some no ruído; com a UF, 3%. Regressão robusta de Huber por serviço, com piso de
5% no MAD (um serviço com tarifa quase determinística marcaria qualquer centavo como outlier).

**D16. Ticket alto só é sinalizado; nunca removido nem winsorizado na receita.** Alternativa:
winsorizar no p99,5. Motivo: o pedido grande é o negócio (B2B, Black Friday). A versão
winsorizada (`total_winsor_centavos`) existe só como feature de modelagem.

**D17. Imputação por modelo para o custo de frete.** Comparados: exclusão, mediana por serviço ×
faixa de peso, gradient boosting com peso, valor cobrado, serviço e transportadora. O modelo tem
o menor erro em MCAR e MAR e o menor viés no custo médio. Nenhum resolve o MNAR; o relatório
diz isso em vez de esconder.

**D18. `+alias` sai da chave de identidade do e-mail.** Alternativa: manter o e-mail como
digitado. Motivo: `maria+promo@x.com` é a mesma caixa; sem unificar, a recompra cai.

**D19. Similaridade de cidade só dentro da UF, Damerau-Levenshtein ≥ 80.** WRatio com corte 88
deixava passar "Salavdor" (transposição conta 2 erros no Levenshtein comum). Dentro da UF há
poucas cidades candidatas, então o corte mais baixo é seguro; fora da UF, nunca.

**D20. Categoria sem correspondência fica nula e sinalizada.** "Denim" não está na tabela de
sinônimos v1 de propósito: mostra o fluxo de revisão (o valor aparece em `categoria_nao_mapeada`
e entra na próxima versão da tabela) em vez de um palpite.

**D21. Fuso corrigido por segmento.** Alternativa: corrigir só as linhas com pagamento "antes"
da compra. Motivo: pedidos pagos dias depois também estão deslocados, só não aparecem no sinal.
O segmento (canal × mês) com 20%+ de lags negativos é corrigido inteiro; na borda do mês, a
assinatura exata (lag entre −3 h e 0) corrige a linha.

**D22. Separador decimal perdido aceito entre 50x e 120x a tabela.** A primeira versão usava
80–120 e perdia itens em promoção (preço a 60% da tabela vira 60x). A avaliação mostrou isso.

## Avaliação e apresentação

**D23. "Antes" = staging, não raw.** Comparar com o texto cru seria injusto: "1.234,56" lido
como texto não é número. Staging é o que um analista teria carregando o CSV com cuidado.

**D24. Precisão e recall por unidade de análise.** Valores de pedido, preços de item, dias,
duplicatas e não-vendas são avaliados separados, cada um com a sua população; misturar daria um
número que não diz nada.

**D25. Português na documentação e no dashboard.** O pedido, a origem e o público (cliente e
marketing brasileiros) estão em português; o código usa os nomes da origem.

**D26. Dashboard estático (HTML + Plotly via CDN) com os dados embutidos.** Sem servidor, abre
no GitHub Pages, e `data.json` fica ao lado para quem quiser os números. O gráfico "quanto da
base estava suja" é HTML puro: os rótulos longos não cabem num eixo Plotly no celular.
