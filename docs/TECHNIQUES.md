# Guia das técnicas

Cada técnica tem duas leituras: **em uma frase** (para quem é de marketing ou negócio) e
**como funciona** (para quem vai implementar). Os números citados são deste projeto, medidos
contra o registro de defeitos, e estão completos em [`reports/EVALUATION.md`](../reports/EVALUATION.md).

> **A pergunta que guia tudo:** este número está estranho porque está **errado** ou porque
> **aconteceu algo**? Black Friday, um cliente que compra 100 vezes mais e um produto que
> viraliza são estranhos e verdadeiros. Uma técnica que não separa os dois apaga justamente o
> que a empresa mais quer ver.

---

## 1. Outliers (valores fora do padrão)

### IQR (intervalo interquartil)

**Em uma frase:** marca o que está muito acima ou abaixo da "metade do meio" dos dados.

**Como funciona:** `Q1` e `Q3` são os quartis de 25% e 75%; `IQR = Q3 − Q1`. Outlier é
`x < Q1 − 1,5·IQR` ou `x > Q3 + 1,5·IQR`.

**Quando serve:** dado aproximadamente simétrico, olhada rápida num gráfico de caixa.
**Quando distorce:** ticket de e-commerce é muito assimétrico (cauda longa de compras grandes).
A cerca de cima corta a cauda inteira. Aqui, aplicado a frete, desconto e total, marcou
34.697 pedidos para achar 942 erros (precisão de 2,7%) e derrubou 2.499 pedidos legítimos.

### z-score (média e desvio padrão)

**Em uma frase:** quantos "desvios" o valor está longe da média.

**Como funciona:** `z = (x − média) / desvio`; outlier se `|z| > 3`.

**Por que quase nunca usar para limpeza:** média e desvio são puxados pelos próprios erros.
Um frete de R$ 9.999,99 infla o desvio de todo mundo e esconde os erros menores. Com 15% de
lixo, os outliers ficam com z ≈ 2,3 e passam (teste em `tests/test_rules.py`).

### z robusto (mediana e MAD)

**Em uma frase:** o mesmo z-score, mas medido a partir do "valor típico", que não se deixa
puxar pelos erros.

**Como funciona:** `MAD = mediana(|x − mediana(x)|)`;
`z_robusto = (x − mediana) / (1,4826·MAD)`; outlier se `|z_robusto| > 3,5` (Iglewicz & Hoaglin).
O fator 1,4826 deixa o MAD na mesma escala do desvio padrão quando os dados são normais.

**Por que MAD e não desvio padrão:** a mediana ignora até 50% de valores ruins. **Por grupo**
(ticket comparado dentro do tipo de cliente, frete dentro do serviço) compara cada um com seus
pares: um pedido de R$ 40 mil de uma confecção não é comparado com o de quem costura por hobby.

### Modelo robusto (regressão de Huber + MAD do resíduo)

**Em uma frase:** primeiro prevê quanto o valor deveria ser (frete para aquele peso e destino)
e só então pergunta se ele está longe do previsto.

**Como funciona:** por serviço de entrega,
`log(frete) = a + b·log(peso) + efeito_UF`, ajustado com perda de Huber (erros grandes pesam
linearmente, não ao quadrado, e não arrastam a reta). `z = resíduo / (1,4826·MAD)`, com piso
de 5%. Com a UF, o resíduo típico cai de 25% para 3%: é isso que faz aparecer um frete trocado
entre um pedido de São Paulo e um de Roraima.

### STL (decomposição sazonal) para séries diárias

**Em uma frase:** separa a receita do dia em tendência, padrão da semana e "sobra"; só a sobra
grande é suspeita.

**Como funciona:** `receita_dia = tendência + sazonalidade_semanal + resíduo` (STL robusta,
período 7, sobre `log(1 + receita)`). Alerta se o z robusto do resíduo passa de 4.

**Sozinho ainda erra:** a Black Friday é um pico anual, não semanal, então vira resíduo. **Com o
calendário comercial** (Black Friday, Dia das Mães, promoções), o pico explicado vira "esperado"
e o resto vira alerta. Aqui: IQR global na receita diária marcou 28 dias e nenhum era um dos
2 dias reenviados. Um dia comum dobrado (R$ 64–67 mil) continua abaixo da cerca (R$ 70 mil),
que é definida pelos picos reais. O STL achou 1 dos 2, e com o calendário comercial deixou de
marcar os 5 dias legítimos que o STL sozinho marcava.

### Isolation Forest

**Em uma frase:** acha combinações raras de várias colunas ao mesmo tempo.

**Como funciona:** árvores aleatórias cortam os dados em pedaços; o que fica isolado com poucos
cortes é raro. Usa total, frete, peso, desconto/subtotal, frete/kg e número de itens; marca o 1%
mais isolado. Score = `2^(−E[h(x)]/c(n))`.

**Quando serve:** triagem para um analista olhar, quando não há regra que descreva o problema.
**Limite:** marca o raro, não o errado. Aqui teve 38% de precisão, 55% de recall e marcou 142
pedidos legítimos; o raro inclui o B2B e a Black Friday.

### Regras de negócio

**Em uma frase:** o que a empresa sabe que é sempre verdade.

**Como funciona:** `total = subtotal − desconto + frete`; `frete do pedido = frete da
transportadora`; `desconto ≤ subtotal`; `preço entre 55% e 100% da tabela`; `entrega ≥ postagem`.

**Por que vêm primeiro:** quando a regra tem uma segunda fonte, ela não só detecta como
**recupera o valor exato**. Em preço de item, 100% de precisão e 100% de recall.

---

## 2. Tratamento: o que fazer com o que foi encontrado

| Ação | Quando | Exemplo neste projeto |
| --- | --- | --- |
| **Remover** | o registro não é uma venda | duplicata, pedido de teste, bot |
| **Corrigir** | existe uma segunda fonte que dá o valor exato | preço zero pelo subtotal; frete pela transportadora; ano da data pelo lote |
| **Imputar** | o valor certo não existe, mas pode ser estimado | frete implausível pelo modelo; custo de frete ausente |
| **Limitar (winsorizar)** | só para alimentar modelo, nunca a receita | `total_winsor_centavos` no p99,5 do segmento |
| **Anular** | o valor é impossível e não há candidato | data de entrega sem correção plausível |
| **Sinalizar** | estranho mas possível | ticket atípico para o segmento; CEP de outra UF |
| **Quarentena** | falta o pai ou passou da janela | item sem pedido; pedido que chegou 20 dias depois |

**Por que winsorizar e não remover, e só em modelo:** remover um pedido grande apaga receita
real; winsorizar a receita a subestima. Para um modelo de previsão, porém, um pedido de
R$ 100 mil puxa os coeficientes, e limitar no p99,5 do segmento estabiliza o ajuste sem jogar
o pedido fora.

---

## 3. Nulos: o mecanismo antes do método

| Mecanismo | Em uma frase | Exemplo aqui | Excluir as linhas |
| --- | --- | --- | --- |
| **MCAR** (ausente completamente ao acaso) | falta sem motivo | 4% dos custos de frete somem aleatoriamente | não enviesa, só perde amostra |
| **MAR** (ausente ao acaso, condicionado) | falta por causa de outra coluna que se vê | a Jadlog não devolve o custo em 30% dos envios | enviesa; condicionar na coluna corrige |
| **MNAR** (ausente não ao acaso) | falta por causa do próprio valor | os fretes mais caros têm a fatura contestada e somem | enviesa; nada com dados observados corrige por inteiro |

**Como diagnosticar:** comparar a taxa de ausência entre grupos (qui-quadrado por
transportadora) e a distribuição de um proxy entre linhas com e sem valor (Kolmogorov-Smirnov no
peso e no valor cobrado). Ausência que varia por grupo = não é MCAR. Dados observados não
separam MAR de MNAR; o documento diz isso em vez de fingir.

**Métodos comparados** (`reports/imputacao.csv`): exclusão, mediana por serviço × faixa de
peso, e gradient boosting (peso, valor cobrado, serviço, transportadora). O modelo erra
R$ 2,6–2,8 por envio em MCAR/MAR contra R$ 5,1–5,5 da mediana; no MNAR todos erram mais de R$ 10
e para baixo. Excluir os nulos subestima o custo médio de frete em 4%.

---

## 4. Duplicatas

- **Exatas:** hash do conteúdo de negócio da linha (sem metadados de lote). Pega paginação
  repetida e lote reenviado. 100% de precisão.
- **Aproximadas:** o reenvio do checkout tem outro id, outro número e outro horário; nenhum hash
  pega. Critério: mesmo cliente, até 10 minutos, itens com Jaccard ≥ 0,9
  (`|A ∩ B| / |A ∪ B|` sobre pares variante:quantidade) e total a 1%. Só a janela de tempo
  tem 85% de precisão: confunde bot e cliente fiel com reenvio. Com os itens, 99,2%.
- **De cliente:** a mesma pessoa com `maria+loja@` e `maria@` vira um cliente pela chave de
  e-mail canônico.

---

## 5. Padronização e normalização

### Texto

Ordem: normalizar (sem acento, minúsculas, espaços simples, sem sufixo "- SE") → tabela de
sinônimos versionada (`config/mappings/*.csv`: "Sampa" → São Paulo, "PV" → Malha PV) →
similaridade (Damerau-Levenshtein, que conta uma transposição como um erro) **dentro da UF** →
sem correspondência, sinaliza. Nunca chuta fora da UF.

### Números e moeda

Com vírgula, a vírgula é o decimal e o ponto é milhar (`1.234,56`); sem vírgula, o ponto é o
decimal. Tudo vira **centavos inteiros**: `0,1 + 0,2 ≠ 0,3` em ponto flutuante, e a identidade
`total = subtotal − desconto + frete` precisa fechar no centavo.

### Datas

Três formatos no mesmo arquivo: `2024-05-03 13:22:01` (hora local), `03/05/2024 13:22:01`
(local) e `2024-05-03T16:22:01Z` (UTC explícito). Tudo é guardado em **UTC** e apresentado em
**America/Sao_Paulo**; o "dia" do pedido para o negócio é o dia em São Paulo.

### Escalas para modelagem

Exemplo com o ticket de cada pedido pago (`reports/escalas.csv`):

| Escala | Fórmula | Assimetria depois | O que acontece |
| --- | --- | ---: | --- |
| original | x | ~67 | cauda enorme |
| min-max | (x − mín) / (máx − mín) | ~67 | **distorce:** um pedido B2B vira 1 e 100% dos outros ficam espremidos em 0–0,1 |
| z-score | (x − média) / desvio | ~67 | **distorce:** média e desvio puxados pela cauda; mesma forma |
| escala robusta | (x − mediana) / IQR | ~67 | centro estável, mas a forma não muda (é linear) |
| log(1 + x) | ln(1 + x) | ~0,5 | **ajuda:** cauda vira quase simétrica; só para x ≥ 0 |
| Yeo-Johnson | potência ajustada por máxima verossimilhança | ~0 | **ajuda:** o mais próximo de normal; aceita zero e negativo |

Lição: min-max, z-score e escala robusta são lineares e **não mudam a forma** da distribuição;
servem para pôr variáveis na mesma escala, não para domar assimetria. Para assimetria, log ou
Yeo-Johnson. E nenhuma escala é limpeza: um erro x100 continua erro depois do log.
