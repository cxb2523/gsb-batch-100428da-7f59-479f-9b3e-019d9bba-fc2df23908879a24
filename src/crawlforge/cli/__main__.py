"""crawlforge CLI."""

from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from crawlforge.engine.crawler import CrawlConfig
from crawlforge.engine.runner import run_crawl_sync

app = typer.Typer(
    name="crawlforge",
    help="LLM-native open source SEO crawler.",
    no_args_is_help=True,
)
console = Console()


@app.command()
def run(
    url: str = typer.Argument(..., help="URL inicial del crawl"),
    max_urls: int = typer.Option(500, "--max-urls", "-n", help="Máximo de URLs"),
    max_depth: int = typer.Option(10, "--max-depth", "-d", help="Profundidad máxima"),
    concurrency: int = typer.Option(8, "--concurrency", "-c", help="Peticiones simultáneas"),
    db: Path = typer.Option(
        Path("crawl.duckdb"), "--db", help="Ruta del fichero DuckDB de salida"
    ),
    same_host: bool = typer.Option(True, "--same-host/--any-host"),
    respect_robots: bool = typer.Option(True, "--respect-robots/--ignore-robots"),
    persist_html: bool = typer.Option(False, "--persist-html"),
) -> None:
    """Crawlea un sitio y guarda el resultado en DuckDB con todas las reglas evaluadas."""
    config = CrawlConfig(
        start_url=url,
        max_urls=max_urls,
        max_depth=max_depth,
        concurrency=concurrency,
        same_host_only=same_host,
        respect_robots=respect_robots,
        persist_html=persist_html,
    )

    if db.exists():
        db.unlink()

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
        transient=True,
    ) as progress:
        task = progress.add_task(f"Crawleando {url}…", total=None)

        def cb(n: int, row: dict) -> None:
            progress.update(task, description=f"[{n}/{max_urls}] {row['url']}")

        con, summary = run_crawl_sync(config, db_path=db, progress_callback=cb)

    console.print(f"\n[bold green]Crawl finalizado[/bold green] en {summary['duration_s']:.1f}s")
    console.print(f"  URLs:      {summary['urls_total']}")
    console.print(
        f"  Fallidas:  {summary['urls_failed']}"
        f" (robots.txt: {summary['urls_blocked_by_robots']},"
        f" errores de red: {summary['urls_request_errors']})"
    )
    console.print(f"  Reglas:    {summary['rules_run']}")
    console.print(f"  Issues:    {summary['issues_total']}")
    console.print(f"  Salida:    {db}")

    issue_summary = con.execute(
        """
        SELECT severity, COUNT(*) AS n
        FROM issues
        GROUP BY severity
        ORDER BY CASE severity
            WHEN 'critical' THEN 0
            WHEN 'warning'  THEN 1
            WHEN 'info'     THEN 2
        END
        """
    ).fetchall()
    if issue_summary:
        table = Table(title="Issues por severidad")
        table.add_column("Severidad")
        table.add_column("Count", justify="right")
        for sev, n in issue_summary:
            table.add_row(sev, str(n))
        console.print(table)

    top_rules = con.execute(
        """
        SELECT rule_id, severity, COUNT(*) AS n
        FROM issues
        GROUP BY rule_id, severity
        ORDER BY n DESC
        LIMIT 10
        """
    ).fetchall()
    if top_rules:
        table = Table(title="Top 10 reglas con más hits")
        table.add_column("Regla")
        table.add_column("Severidad")
        table.add_column("Count", justify="right")
        for rid, sev, n in top_rules:
            table.add_row(rid, sev, str(n))
        console.print(table)


@app.command()
def query(
    db: Path = typer.Argument(..., help="Fichero DuckDB"),
    sql: str = typer.Argument(..., help="Query SQL"),
    limit: int = typer.Option(50, "--limit", "-l"),
) -> None:
    """Ejecuta una query SQL arbitraria sobre el crawl."""
    import duckdb

    con = duckdb.connect(str(db), read_only=True)
    rows = con.execute(sql + (f" LIMIT {limit}" if "limit" not in sql.lower() else "")).fetchall()
    cols = [d[0] for d in con.description] if con.description else []

    if not rows:
        console.print("[dim]Sin resultados.[/dim]")
        return
    table = Table()
    for c in cols:
        table.add_column(c)
    for row in rows:
        table.add_row(*[str(v)[:80] if v is not None else "" for v in row])
    console.print(table)


@app.command()
def rules() -> None:
    """Lista todas las reglas registradas."""
    from crawlforge.rules._registry import discover_rules

    discovered = discover_rules()
    table = Table(title=f"Reglas registradas ({len(discovered)})")
    table.add_column("ID")
    table.add_column("Categoría")
    table.add_column("Severidad")
    table.add_column("Descripción")
    for rule_cls in sorted(discovered.values(), key=lambda c: (c.category, c.id)):
        table.add_row(rule_cls.id, rule_cls.category, rule_cls.severity, rule_cls.description[:60])
    console.print(table)


@app.command()
def info(db: Path = typer.Argument(...)) -> None:
    """Resumen del crawl almacenado en un .duckdb."""
    import duckdb

    con = duckdb.connect(str(db), read_only=True)
    meta = dict(con.execute("SELECT key, value FROM crawl_meta").fetchall())
    console.print(json.dumps(meta, indent=2, default=str))


if __name__ == "__main__":
    app()
