# Validador Shopify

Script Python, só com a biblioteca padrão, que confirma os problemas apontados na revisão
do `weekly-report-review` e gera e verifica a correção.

## Como correr

```bash
python validador_shopify.py
```

Sem `--repo`, clona sozinho `AERO-AGENCY/weekly-report-review`, faz checkout do commit do
exercício e instala as dependências. Para usar um clone já existente:

```bash
python validador_shopify.py --repo "caminho/para/weekly-report-review"
```

## O que faz

1. Confirma o commit e corre `npm run typecheck` no repositório original, sem o alterar.
2. Reproduz os 6 problemas da revisão: segredo em logs, retry duplicado, fuso horário,
   fan-out de pedidos, dados em falta e backoff sem Retry-After.
3. Aplica a correção (`../codigo-corrigido/weeklySalesReport.ts` e
   `../codigo-corrigido/shopify.ts`) numa cópia temporária e corre o typecheck outra vez
   para confirmar que compila.
4. Confirma, ponto a ponto, que cada problema foi corrigido.

## Exemplo de execução

```
$ python validador_shopify.py
### R. e Agents AI ###
Validador Shopify - AERO Weekly Report Review
Confirmados: 6 | Bloqueantes: 5 | Não bloqueantes: 1
Correção verificada, typecheck exit 0, todos corrigidos: True
Relatórios: <pasta>/output
```

Este teste foi corrido de verdade, contra o repositório real do exercício. O log completo
fica em `output/`: `r-agents-validation.md` para leitura, `r-agents-validation.json` com o
detalhe estruturado, e `r-agents-events.jsonl` com o histórico da execução. Um exemplo já
corrido está incluído nesta pasta.

## Contacto

contact@rewebfolio.xyz
