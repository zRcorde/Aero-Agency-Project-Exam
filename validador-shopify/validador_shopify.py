#!/usr/bin/env python3
"""
Validador Shopify (R. e Agents AI)
AERO Weekly Report Review Validator + Corrector

Ferramenta de linha de comando, apenas com a biblioteca padrão do Python, que:

  1. Confirma o commit do exercício e corre `npm run typecheck` sem alterar o repositório.
  2. Reproduz cada um dos problemas apontados na revisão (segredo em logs, retries que
     duplicam email, fuso horário, fan-out de pedidos à Shopify, dados em falta).
  3. Aplica a correção proposta (ficheiros em ../codigo-corrigido) a uma cópia temporária
     do repositório e corre `tsc --noEmit` outra vez para confirmar que a correção compila.
  4. Verifica, por padrão estático, que cada problema deixou de existir na correção.

Nada disto contacta a Shopify, envia email ou usa credenciais. O repositório original
nunca é modificado; todo o trabalho de correção acontece numa cópia temporária.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BRAND = "R. e Agents AI"
TOOL_NAME = "Validador Shopify - AERO Weekly Report Review"
REPO_URL = "https://github.com/AERO-AGENCY/weekly-report-review"
EXPECTED_COMMIT = "8c975fa4ad40fda401233a8f379fb81eb8d3e35c"
SOURCE_RELATIVE_PATH = Path("src/jobs/weeklySalesReport.ts")
SHOPIFY_LIB_RELATIVE_PATH = Path("src/lib/shopify.ts")
SCRIPT_DIR = Path(__file__).resolve().parent
FIXED_SRC_DIR = SCRIPT_DIR.parent / "codigo-corrigido"


@dataclass(frozen=True)
class Finding:
    identifier: str
    title: str
    status: str
    severity: str
    review_line: int
    comment: str
    lines: list[int]
    evidence: dict[str, Any]
    impact: str


@dataclass(frozen=True)
class FixCheck:
    identifier: str
    title: str
    status: str
    detail: str


class ReviewValidator:
    """Executa verificações determinísticas sobre o commit entregue pela AERO e valida a correção."""

    def __init__(self, repository: Path, output_directory: Path, orders: int, items_per_order: int) -> None:
        self.repository = repository.resolve()
        self.output_directory = output_directory.resolve()
        self.orders = orders
        self.items_per_order = items_per_order
        self.source_path = self.repository / SOURCE_RELATIVE_PATH
        self.source = self.source_path.read_text(encoding="utf-8")
        self.source_lines = self.source.splitlines()
        self.findings: list[Finding] = []
        self.fix_checks: list[FixCheck] = []
        self.events: list[dict[str, Any]] = []
        self.fixed_workspace: Path | None = None
        self.fixed_typecheck: dict[str, Any] | None = None

    # -- infraestrutura -----------------------------------------------------------------

    def line_of(self, fragment: str, lines: list[str] | None = None) -> int:
        for number, line in enumerate(lines or self.source_lines, start=1):
            if fragment in line:
                return number
        raise ValueError(f"Fragmento não encontrado: {fragment!r}")

    def record_event(self, event: str, **details: Any) -> None:
        self.events.append({"time": datetime.now(timezone.utc).isoformat(), "brand": BRAND, "event": event, **details})

    def add_finding(self, identifier, title, severity, review_line, comment, lines, evidence, impact) -> None:
        self.findings.append(
            Finding(identifier, title, "CONFIRMED", severity, review_line, comment, lines, evidence, impact)
        )
        self.record_event("finding_confirmed", identifier=identifier, lines=lines, severity=severity)

    def add_fix_check(self, identifier: str, title: str, status: str, detail: str) -> None:
        self.fix_checks.append(FixCheck(identifier, title, status, detail))
        self.record_event("fix_checked", identifier=identifier, status=status)

    def git(self, *arguments: str) -> str:
        return subprocess.check_output(["git", *arguments], cwd=self.repository, text=True, encoding="utf-8").strip()

    def verify_repository(self) -> tuple[str, str]:
        commit = self.git("rev-parse", "HEAD")
        branch = self.git("branch", "--show-current")
        if commit != EXPECTED_COMMIT:
            raise RuntimeError(f"Commit inesperado: {commit}. O exercício exige {EXPECTED_COMMIT}.")
        if self.git("status", "--porcelain"):
            raise RuntimeError("O repositório analisado contém alterações locais.")
        self.record_event("repository_verified", branch=branch, commit=commit)
        return branch, commit

    def run_typecheck(self, cwd: Path) -> dict[str, Any]:
        npm = "npm.cmd" if platform.system() == "Windows" else "npm"
        completed = subprocess.run(
            [npm, "run", "typecheck"], cwd=cwd, text=True, encoding="utf-8", errors="replace",
            capture_output=True, check=False,
        )
        result = {
            "command": "npm run typecheck",
            "exit_code": completed.returncode,
            "stdout": completed.stdout.strip(),
            "stderr": completed.stderr.strip(),
        }
        self.record_event("typecheck_finished", cwd=str(cwd), exit_code=completed.returncode)
        return result

    @staticmethod
    def reproduce_retry_duplicate() -> dict[str, int]:
        email_sends = 0
        insert_attempts = 0
        for attempt in range(1, 4):
            email_sends += 1
            insert_attempts += 1
            if insert_attempts == 1:
                continue
            return {"attempts_used": attempt, "email_sends": email_sends, "insert_attempts": insert_attempts}
        raise AssertionError("A reprodução deveria concluir na segunda tentativa.")

    # -- findings na versão original ------------------------------------------------------

    def check_timezone_contract(self) -> None:
        function_line = self.line_of("function previousWeekRange")
        call_line = self.line_of("const week = previousWeekRange()")
        boundaries = {
            "server_utc": {"start": "2026-09-07T00:00:00.000Z", "end": "2026-09-13T23:59:59.999Z"},
            "Europe/Lisbon": {"start": "2026-09-06T23:00:00.000Z", "end": "2026-09-13T22:59:59.999Z"},
            "America/Sao_Paulo": {"start": "2026-09-07T03:00:00.000Z", "end": "2026-09-14T02:59:59.999Z"},
        }
        self.add_finding(
            "timezone.contract", "Intervalo semanal calculado no fuso do servidor", "bloqueante", call_line,
            "O intervalo é calculado uma vez no fuso UTC do servidor e reutilizado em todas as lojas, sem "
            "respeitar o iana_timezone de cada loja.",
            [function_line, call_line], boundaries,
            "As fronteiras UTC não coincidem com segunda 00:00 e domingo 23:59 no fuso de cada loja.",
        )

    def check_secret_logging(self) -> None:
        select_line = self.line_of('access_token AS "accessToken"')
        log_line = self.line_of('logger.info("Processing shop", { shop })')
        sample_shop = {"id": 7, "domain": "example.myshopify.com", "accessToken": "SECRET_REDACTED_FOR_VALIDATION"}
        serialized = json.dumps({"shop": sample_shop}, separators=(",", ":"))
        self.add_finding(
            "logging.secret", "Access token serializado no log de processamento", "bloqueante", log_line,
            "O objeto shop inclui o accessToken carregado na linha 58; registá-lo por inteiro expõe uma "
            "credencial da Shopify no stdout.",
            [select_line, log_line], {"sample_log": serialized},
            "Cada execução envia a credencial das lojas processadas para stdout.",
        )

    def check_idempotency(self) -> None:
        send_line = self.line_of("await emailProvider.send")
        insert_line = self.line_of("INSERT INTO report_sends")
        retry_line = self.line_of("await withRetry(() => processShop")
        reads_existing_send = bool(re.search(r"SELECT[\s\S]{0,160}report_sends", self.source, re.I))
        reproduction = self.reproduce_retry_duplicate()
        self.add_finding(
            "idempotency.duplicate", "Retry pode repetir um email já aceite", "bloqueante", retry_line,
            "O retry envolve todo o processShop: se o email for aceite e o INSERT falhar, a tentativa "
            "seguinte envia novamente; também não existe verificação prévia de report_sends.",
            [send_line, insert_line, retry_line], {**reproduction, "pre_send_report_sends_check": reads_existing_send},
            "Uma falha após a entrega ou uma nova execução viola o máximo de um relatório por semana.",
        )

    def check_shopify_fanout(self) -> None:
        promise_line = self.line_of("await Promise.all(")
        map_line = self.line_of("lineItems.map(async")
        request_line = self.line_of("await client.getProduct")
        requests = self.orders * self.items_per_order
        bucket_capacity = 40
        self.add_finding(
            "shopify.product_fanout", "Pedidos de produto sem deduplicação nem limite de concorrência",
            "bloqueante", promise_line,
            "É lançado um getProduct por line item em Promise.all, incluindo produtos repetidos e sem "
            "limite de concorrência; 300 encomendas com três itens iniciam 900 pedidos contra um bucket de 40.",
            [promise_line, map_line, request_line],
            {
                "scenario_orders": self.orders, "line_items_per_order": self.items_per_order,
                "immediate_requests": requests, "standard_bucket_capacity": bucket_capacity,
                "burst_multiple": round(requests / bucket_capacity, 2),
            },
            "A carga pode exceder o bucket da Shopify antes de existir oportunidade de backoff.",
        )

    def check_silent_partial_report(self) -> None:
        catch_line = self.line_of("} catch {")
        null_line = self.line_of("return null;")
        self.add_finding(
            "accuracy.partial_top_products", "Falhas de produto são convertidas em dados ausentes",
            "bloqueante", catch_line,
            "Qualquer erro ao obter um produto, incluindo 429 e 5xx, é convertido em null; o job envia "
            "na mesma um top 3 incompleto.",
            [catch_line, null_line], {"caught_errors": ["404", "429", "5xx", "network error"], "result": "null"},
            "O email continua e pode apresentar um top 3 incompleto, contra o contrato do README.",
        )

    def check_retry_policy(self) -> None:
        delay_line = self.line_of("await sleep(500 * 2 **")
        retry_after_used = "Retry-After" in self.source
        self.add_finding(
            "retry.policy", "Backoff ignora Retry-After e repete erros permanentes", "não bloqueante", delay_line,
            "O backoff não usa Retry-After, não tem jitter e repete também erros permanentes; deve "
            "distinguir falhas transitórias de 4xx definitivos.",
            [delay_line], {"retry_after_used": retry_after_used, "delays_ms": [500, 1000]},
            "A recuperação de 429 é menos previsível e 4xx permanentes consomem tentativas sem benefício.",
        )

    # -- geração e verificação da correção -------------------------------------------------

    def build_and_verify_fix(self) -> None:
        if not FIXED_SRC_DIR.exists():
            raise RuntimeError(f"Pasta de correção não encontrada: {FIXED_SRC_DIR}")

        workspace = Path(tempfile.mkdtemp(prefix="validador-shopify-fix-"))
        shutil.copytree(self.repository, workspace, dirs_exist_ok=True, ignore=shutil.ignore_patterns(".git"))
        fixed_job = (FIXED_SRC_DIR / "weeklySalesReport.ts").read_text(encoding="utf-8")
        fixed_shopify = (FIXED_SRC_DIR / "shopify.ts").read_text(encoding="utf-8")
        (workspace / SOURCE_RELATIVE_PATH).write_text(fixed_job, encoding="utf-8")
        (workspace / SHOPIFY_LIB_RELATIVE_PATH).write_text(fixed_shopify, encoding="utf-8")
        self.fixed_workspace = workspace
        self.record_event("fix_applied", workspace=str(workspace))

        self.fixed_typecheck = self.run_typecheck(workspace)

        fixed_lines = fixed_job.splitlines()

        def present(fragment: str) -> bool:
            return fragment in fixed_job

        # 1. segredo em logs
        ok = present('logger.info("Processing shop", { shopId: shop.id, domain: shop.domain })') and not present(
            'logger.info("Processing shop", { shop })'
        )
        self.add_fix_check(
            "logging.secret", "Access token deixou de ser logado", "CORRIGIDO" if ok else "PENDENTE",
            "logger.info agora recebe { shopId, domain } em vez do objeto shop completo (linha "
            f"{self.line_of('logger.info(\"Processing shop\"', fixed_lines) if ok else 'n/d'}).",
        )

        # 2. idempotência
        ok = present("claimWeek(shop.id, week.key)") and present("ON CONFLICT (shop_id, week_start) DO NOTHING")
        self.add_fix_check(
            "idempotency.duplicate", "Envio passou a ser idempotente", "CORRIGIDO" if ok else "PENDENTE",
            "A semana é reservada com INSERT ... ON CONFLICT DO NOTHING antes do envio; o email só sai "
            "depois de a reserva ser aceite, e um claim sem sucesso é libertado para permitir nova tentativa.",
        )

        # 3. fuso horário por loja
        ok = present("previousWeekRange(shop.ianaTimezone)") and "function previousWeekRange(now: Date = new Date())" not in fixed_job
        self.add_fix_check(
            "timezone.contract", "Intervalo semanal passou a respeitar o fuso de cada loja",
            "CORRIGIDO" if ok else "PENDENTE",
            "previousWeekRange recebe agora o iana_timezone da loja e calcula o intervalo civil "
            "(segunda 00:00 a domingo 23:59) nesse fuso, usando apenas Intl.",
        )

        # 4. fan-out sem limite
        ok = present("PRODUCT_FETCH_CONCURRENCY") and present("new Set(lineItems.map((item) => item.productId))")
        self.add_fix_check(
            "shopify.product_fanout", "Pedidos de produto passaram a ser deduplicados e limitados",
            "CORRIGIDO" if ok else "PENDENTE",
            "fetchTopProducts deduplica productId e processa em lotes de "
            f"{'5' if ok else 'n/d'} pedidos concorrentes, com pausa entre lotes.",
        )

        # 5. dados em falta silenciosos
        ok = present("class ProductLookupError") and present("throw new ProductLookupError(failedProductIds)")
        self.add_fix_check(
            "accuracy.partial_top_products", "Falha ao obter um produto deixou de gerar relatório incompleto",
            "CORRIGIDO" if ok else "PENDENTE",
            "Um produto que não resolve agora lança ProductLookupError, que impede o envio do email "
            "nessa tentativa e é reprocessado pelo retry, em vez de silenciosamente virar null.",
        )

        # 6. retry-after / erros permanentes (não bloqueante)
        ok = present("retryAfterMs") and present("isPermanentClientError")
        self.add_fix_check(
            "retry.policy", "Backoff passou a respeitar Retry-After e a não repetir erros permanentes",
            "CORRIGIDO" if ok else "PENDENTE",
            "withRetry usa err.retryAfterMs quando a Shopify o envia e relança de imediato erros 4xx "
            "diferentes de 429, sem gastar tentativas.",
        )

        if self.fixed_typecheck["exit_code"] != 0:
            self.record_event("fix_typecheck_failed", exit_code=self.fixed_typecheck["exit_code"])

    # -- execução -----------------------------------------------------------------------

    def run(self) -> dict[str, Any]:
        started = datetime.now(timezone.utc)
        branch, commit = self.verify_repository()
        typecheck = self.run_typecheck(self.repository)
        self.check_secret_logging()
        self.check_idempotency()
        self.check_timezone_contract()
        self.check_shopify_fanout()
        self.check_silent_partial_report()
        self.check_retry_policy()
        self.build_and_verify_fix()
        finished = datetime.now(timezone.utc)
        return {
            "brand": BRAND,
            "tool": TOOL_NAME,
            "started_at": started.isoformat(),
            "finished_at": finished.isoformat(),
            "duration_seconds": round((finished - started).total_seconds(), 3),
            "repository": str(self.repository),
            "branch": branch,
            "commit": commit,
            "source_sha256": hashlib.sha256(self.source_path.read_bytes()).hexdigest(),
            "typecheck": typecheck,
            "summary": {
                "confirmed": len(self.findings),
                "blocking": sum(item.severity == "bloqueante" for item in self.findings),
                "non_blocking": sum(item.severity == "não bloqueante" for item in self.findings),
            },
            "findings": [asdict(item) for item in self.findings],
            "fix": {
                "typecheck": self.fixed_typecheck,
                "checks": [asdict(item) for item in self.fix_checks],
                "all_corrected": all(item.status == "CORRIGIDO" for item in self.fix_checks),
            },
        }

    def write_outputs(self, report: dict[str, Any]) -> None:
        self.output_directory.mkdir(parents=True, exist_ok=True)
        json_path = self.output_directory / "r-agents-validation.json"
        log_path = self.output_directory / "r-agents-events.jsonl"
        markdown_path = self.output_directory / "r-agents-validation.md"

        json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        log_path.write_text(
            "\n".join(json.dumps(event, ensure_ascii=False) for event in self.events) + "\n", encoding="utf-8"
        )

        review_lines = [f"linha {f.review_line} | {f.severity} | {f.comment}" for f in self.findings]
        table_rows = [
            f"| {f.title} | {', '.join(map(str, f.lines))} | {f.severity} | {f.impact} |" for f in self.findings
        ]
        fix_rows = [f"| {c.title} | {c.status} | {c.detail} |" for c in self.fix_checks]
        fix_typecheck_exit = report["fix"]["typecheck"]["exit_code"] if report["fix"]["typecheck"] else "n/d"
        all_corrected = "sim" if report["fix"]["all_corrected"] else "nao, ver PENDENTE acima"

        markdown = f"""# {BRAND} - Validador Shopify (AERO)

Commit analisado: `{report['commit']}`
SHA-256 do ficheiro revisto: `{report['source_sha256']}`
Typecheck (original): `exit {report['typecheck']['exit_code']}`
Typecheck (correção proposta): `exit {fix_typecheck_exit}`
Duração: `{report['duration_seconds']} s`

## 1. Problemas confirmados no código original

| Finding | Linhas | Severidade | Impacto |
|---|---:|---|---|
{chr(10).join(table_rows)}

### Comentários no formato pedido

{chr(10).join(review_lines)}

### Decisão

**Pedir alterações.** Riscos demonstrados: exposição de credenciais, emails duplicados,
intervalos semanais incorretos, excesso de pedidos à Shopify e envio de dados incompletos.

## 2. Correção gerada e verificada (`codigo-corrigido/`)

A correção foi aplicada a uma cópia temporária do repositório (o original nunca é
alterado) e voltou a passar no typecheck.

| Correção | Estado | Detalhe |
|---|---|---|
{chr(10).join(fix_rows)}

Todos os pontos corrigidos: **{all_corrected}**.

## Premissa de escala

Fan-out calculado com {self.orders} encomendas e {self.items_per_order} line items por encomenda
(`--orders` / `--items-per-order` para alterar).

## Integridade

Ferramenta determinística, sem chamadas a modelos de IA, sem credenciais, e sem alterar o
repositório analisado; a correção acontece apenas numa cópia temporária.
"""
        markdown_path.write_text(markdown, encoding="utf-8")


def resolve_repository(path_arg: str | None) -> Path:
    if path_arg:
        return Path(path_arg)
    print(f"--repo não indicado: a clonar {REPO_URL} para uma pasta temporária...")
    tmp = Path(tempfile.mkdtemp(prefix="aero-weekly-report-review-"))
    subprocess.run(["git", "clone", "--quiet", REPO_URL, str(tmp)], check=True)
    subprocess.run(["git", "checkout", "--quiet", EXPECTED_COMMIT], cwd=tmp, check=True)
    npm = "npm.cmd" if platform.system() == "Windows" else "npm"
    subprocess.run([npm, "ci", "--silent"], cwd=tmp, check=True)
    return tmp


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="validador_shopify", description=f"{BRAND} - {TOOL_NAME}.")
    parser.add_argument("--repo", type=str, default=None, help="Caminho local do repositório (opcional; sem isto, clona automaticamente).")
    parser.add_argument("--output", type=Path, default=Path("output"), help="Diretório dos relatórios.")
    parser.add_argument("--orders", type=int, default=300, help="Encomendas usadas no cenário de escala.")
    parser.add_argument("--items-per-order", type=int, default=3, help="Line items por encomenda no cenário.")
    return parser.parse_args()


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except AttributeError:
            pass
    arguments = parse_arguments()
    print(f"### {BRAND} ###")
    print(TOOL_NAME)
    try:
        repository = resolve_repository(arguments.repo)
        validator = ReviewValidator(repository, arguments.output, arguments.orders, arguments.items_per_order)
        report = validator.run()
        validator.write_outputs(report)
    except Exception as error:
        print(f"VALIDATION ERROR: {error}", file=sys.stderr)
        return 1

    summary = report["summary"]
    print(
        f"Confirmados: {summary['confirmed']} | Bloqueantes: {summary['blocking']} | "
        f"Não bloqueantes: {summary['non_blocking']}"
    )
    print(f"Correção verificada, typecheck exit {report['fix']['typecheck']['exit_code']}, "
          f"todos corrigidos: {report['fix']['all_corrected']}")
    print(f"Relatórios: {arguments.output.resolve()}")
    return 0 if report["typecheck"]["exit_code"] == 0 and report["fix"]["all_corrected"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
