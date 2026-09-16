# Aero Agency Project Exam

Repositório de apoio ao exercício técnico da AERO Agency (revisão do PR
`weekly-report-review`).

## Estrutura

- [`codigo-corrigido/`](./codigo-corrigido): o que o teste pediu. `weeklySalesReport.ts`
  e `shopify.ts` com a correção dos problemas apontados na revisão (segredo em logs,
  retry duplicado, fuso horário, fan-out de pedidos, dados em falta e backoff sem
  `Retry-After`).
- [`validador-shopify/`](./validador-shopify): o script que criei. Confirma cada
  problema contra o commit original, aplica a correção acima numa cópia temporária do
  repositório e volta a correr o typecheck para confirmar que resolve tudo.

## Correr o validador

```bash
cd validador-shopify
python validador_shopify.py
```

Sem `--repo`, clona sozinho `AERO-AGENCY/weekly-report-review`. Não usa credenciais e não
altera nenhum repositório original.

## Contacto

contact@rewebfolio.xyz
