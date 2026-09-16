# R. e Agents AI - Validador Shopify (AERO)

Commit analisado: `8c975fa4ad40fda401233a8f379fb81eb8d3e35c`
SHA-256 do ficheiro revisto: `7d48bca2aa530b836810dc49b3e5433a1bb2b8368f98021115b7436b9e3ffd16`
Typecheck (original): `exit 0`
Typecheck (correção proposta): `exit 0`
Duração: `4.409 s`

## 1. Problemas confirmados no código original

| Finding | Linhas | Severidade | Impacto |
|---|---:|---|---|
| Access token serializado no log de processamento | 58, 140 | bloqueante | Cada execução envia a credencial das lojas processadas para stdout. |
| Retry pode repetir um email já aceite | 120, 126, 142 | bloqueante | Uma falha após a entrega ou uma nova execução viola o máximo de um relatório por semana. |
| Intervalo semanal calculado no fuso do servidor | 36, 133 | bloqueante | As fronteiras UTC não coincidem com segunda 00:00 e domingo 23:59 no fuso de cada loja. |
| Pedidos de produto sem deduplicação nem limite de concorrência | 66, 67, 69 | bloqueante | A carga pode exceder o bucket da Shopify antes de existir oportunidade de backoff. |
| Falhas de produto são convertidas em dados ausentes | 71, 73 | bloqueante | O email continua e pode apresentar um top 3 incompleto, contra o contrato do README. |
| Backoff ignora Retry-After e repete erros permanentes | 29 | não bloqueante | A recuperação de 429 é menos previsível e 4xx permanentes consomem tentativas sem benefício. |

### Comentários no formato pedido

linha 140 | bloqueante | O objeto shop inclui o accessToken carregado na linha 58; registá-lo por inteiro expõe uma credencial da Shopify no stdout.
linha 142 | bloqueante | O retry envolve todo o processShop: se o email for aceite e o INSERT falhar, a tentativa seguinte envia novamente; também não existe verificação prévia de report_sends.
linha 133 | bloqueante | O intervalo é calculado uma vez no fuso UTC do servidor e reutilizado em todas as lojas, sem respeitar o iana_timezone de cada loja.
linha 66 | bloqueante | É lançado um getProduct por line item em Promise.all, incluindo produtos repetidos e sem limite de concorrência; 300 encomendas com três itens iniciam 900 pedidos contra um bucket de 40.
linha 71 | bloqueante | Qualquer erro ao obter um produto, incluindo 429 e 5xx, é convertido em null; o job envia na mesma um top 3 incompleto.
linha 29 | não bloqueante | O backoff não usa Retry-After, não tem jitter e repete também erros permanentes; deve distinguir falhas transitórias de 4xx definitivos.

### Decisão

**Pedir alterações.** Riscos demonstrados: exposição de credenciais, emails duplicados,
intervalos semanais incorretos, excesso de pedidos à Shopify e envio de dados incompletos.

## 2. Correção gerada e verificada (`codigo-corrigido/`)

A correção foi aplicada a uma cópia temporária do repositório (o original nunca é
alterado) e voltou a passar no typecheck.

| Correção | Estado | Detalhe |
|---|---|---|
| Access token deixou de ser logado | CORRIGIDO | logger.info agora recebe { shopId, domain } em vez do objeto shop completo (linha 259). |
| Envio passou a ser idempotente | CORRIGIDO | A semana é reservada com INSERT ... ON CONFLICT DO NOTHING antes do envio; o email só sai depois de a reserva ser aceite, e um claim sem sucesso é libertado para permitir nova tentativa. |
| Intervalo semanal passou a respeitar o fuso de cada loja | CORRIGIDO | previousWeekRange recebe agora o iana_timezone da loja e calcula o intervalo civil (segunda 00:00 a domingo 23:59) nesse fuso, usando apenas Intl. |
| Pedidos de produto passaram a ser deduplicados e limitados | CORRIGIDO | fetchTopProducts deduplica productId e processa em lotes de 5 pedidos concorrentes, com pausa entre lotes. |
| Falha ao obter um produto deixou de gerar relatório incompleto | CORRIGIDO | Um produto que não resolve agora lança ProductLookupError, que impede o envio do email nessa tentativa e é reprocessado pelo retry, em vez de silenciosamente virar null. |
| Backoff passou a respeitar Retry-After e a não repetir erros permanentes | CORRIGIDO | withRetry usa err.retryAfterMs quando a Shopify o envia e relança de imediato erros 4xx diferentes de 429, sem gastar tentativas. |

Todos os pontos corrigidos: **sim**.

## Premissa de escala

Fan-out calculado com 300 encomendas e 3 line items por encomenda
(`--orders` / `--items-per-order` para alterar).

## Integridade

Ferramenta determinística, sem chamadas a modelos de IA, sem credenciais, e sem alterar o
repositório analisado; a correção acontece apenas numa cópia temporária.
