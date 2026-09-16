# Notas técnicas: AERO Agency

## Âmbito verificado

- Repositório: `https://github.com/AERO-AGENCY/weekly-report-review`
- Branch: `main`
- Commit: `8c975fa4ad40fda401233a8f379fb81eb8d3e35c`
- Commit analisado: `Add weekly sales report job`
- Início real: 2026-09-16 19:59:27 +01:00
- Testes existentes: nenhum; `package.json` expõe apenas `typecheck`.
- `npm ci`: concluído, 18 pacotes instalados, 0 vulnerabilidades reportadas.
- `npm run typecheck`: concluído sem erros.

## Findings confirmados

| Finding | Evidência | Classificação | Reprodução / impacto |
|---|---|---|---|
| Token em logs | `weeklySalesReport.ts:58`, `:140`; `logger.ts:4` serializa meta | Bloqueante | Uma execução regista o objeto que contém `accessToken`. |
| Falta de idempotência | `:120` envia, `:126` grava, `:142` repete tudo; nenhuma leitura de `report_sends` | Bloqueante | Falha do INSERT após entrega produziu 2 envios no validador. |
| Intervalo no fuso errado | `:36` a `:44`, calculado uma vez em `:133` | Bloqueante | Para a semana de 7/9/2026, UTC começa `00:00Z`; Lisboa começa `23:00Z` do dia anterior e São Paulo `03:00Z`. |
| Fan-out N+1 sem limite | `:66` a `:75` e `:69` | Bloqueante | Premissa explícita de 300 encomendas com 3 itens, 900 pedidos imediatos, 22,5 vezes o bucket padrão de 40. |
| Erros de produto descartados | `:71` a `:73` | Bloqueante | 429/5xx e produto removido têm o mesmo resultado: item omitido e email enviado com top incompleto. |
| Retry indiferenciado | `:22` a `:32` | Não bloqueante | Não respeita `Retry-After`, não tem jitter e repete 4xx permanentes. |

## Findings eliminados

- Paginação ausente: rejeitado. `ShopifyClient.listOrders` percorre `nextUrl` até `null` nas linhas 87 a 94.
- Falha de uma loja interrompe todas: rejeitado. O `try/catch` está dentro do ciclo nas linhas 139 a 147.
- Loja sem vendas provoca divisão por zero: rejeitado. A linha 91 devolve ticket médio zero.
- Segredo incluído na mensagem de erro da Shopify: rejeitado. A linha 111 inclui apenas status e pathname, não o header.
- Erro obrigatório em encomendas canceladas ou reembolsadas: não incluído. O README não define a semântica financeira com precisão suficiente para afirmar o cálculo correto sem contexto de produto.
- Erro monetário visível por floating point: não incluído. Existe risco técnico, mas não foi demonstrado um valor apresentado incorretamente com os dados disponíveis.
- Corridas entre processos como cenário principal: não usado para exagerar a severidade; o próprio README declara um único processo. A falta de idempotência já é demonstrável em retries e reruns sequenciais.

## Premissas da validação de escala

- A Shopify documenta bucket padrão de 40 pedidos por aplicação/loja e leak rate de 2 pedidos por segundo.
- O cenário de 900 pedidos não é apresentado como média real: é uma premissa explícita de 300 encomendas, número referido no README, com 3 line items por encomenda.
- O código faz um pedido por line item, mesmo quando o produto se repete.

## Artefactos

- `validador-shopify/validador_shopify.py`
- `validador-shopify/output/r-agents-validation.md`
- `validador-shopify/output/r-agents-validation.json`
- `validador-shopify/output/r-agents-events.jsonl`
- `codigo-corrigido/weeklySalesReport.ts`
- `codigo-corrigido/shopify.ts`

## Revisão independente

O Hermes orquestrou o pipeline e delegou a segunda revisão independente ao Codex e ao Claude. Uma segunda instância do Codex analisou o mesmo commit sem receber os findings da primeira revisão, confirmou os cinco bloqueadores, as linhas e o impacto a 500 lojas, e rejeitou como falsos positivos a ausência de paginação, a interrupção global após falha de uma loja, a fuga pelo header e a dependência de uma restrição de base de dados não observável.
