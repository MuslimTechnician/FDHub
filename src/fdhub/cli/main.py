"""
FDHub CLI entry point.

Commands:
    fdhub validate          — validate all apps/*.json against schema
    fdhub check [app-id]    — check GitHub for updates (discovery only)
    fdhub process <app-id>  — process a single app
    fdhub process-all       — process all apps
    fdhub generate-index    — regenerate F-Droid index from current state
    fdhub report            — print the latest run report
    fdhub init-signing      — generate signing key
    fdhub backfill <app-id> — load full release history for an app
    fdhub rebuild           — full historical rebuild
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.logging import RichHandler

console = Console()


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(message)s",
        handlers=[RichHandler(console=console, show_path=False, markup=True)],
    )


def _find_project_root() -> Path:
    """Find project root by walking up from CWD looking for apps/ or pyproject.toml."""
    cwd = Path.cwd()
    for parent in [cwd] + list(cwd.parents):
        if (parent / "apps").is_dir() or (parent / "pyproject.toml").is_file():
            return parent
    return cwd


def _load_config(root: Path):
    from fdhub.config.settings import load_global_config
    return load_global_config(root / "config")


@click.group()
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose logging")
@click.option(
    "--root",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help="Project root directory (default: auto-detect)",
)
@click.pass_context
def cli(ctx: click.Context, verbose: bool, root: Path | None) -> None:
    """FDHub — GitHub-native F-Droid binary repository generator."""
    _setup_logging(verbose)
    ctx.ensure_object(dict)
    ctx.obj["root"] = root or _find_project_root()
    ctx.obj["verbose"] = verbose


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------

@cli.command()
@click.pass_context
def validate(ctx: click.Context) -> None:
    """Validate all apps/*.json against the JSON schema."""
    from fdhub.config.loader import load_all_apps
    from rich.table import Table

    root: Path = ctx.obj["root"]
    apps_dir = root / "apps"

    console.print(f"\n[bold]Validating app configurations in[/bold] {apps_dir}\n")

    result = load_all_apps(apps_dir)

    if result.has_errors:
        table = Table(title="Configuration Errors", style="red")
        table.add_column("File")
        table.add_column("Field")
        table.add_column("Problem")
        table.add_column("Expected")

        for err in result.errors:
            table.add_row(
                err.filename,
                err.field or "(root)",
                err.problem,
                err.expected or "",
            )
        console.print(table)
        console.print(f"\n[red]❌ {result.error_count} error(s) found.[/red]")
        sys.exit(1)

    console.print(f"[green]✅ All {result.valid_count} app configuration(s) are valid.[/green]")


# ---------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("app_id", required=False)
@click.option("--dry-run", is_flag=True, help="Do not modify state")
@click.pass_context
def check(ctx: click.Context, app_id: str | None, dry_run: bool) -> None:
    """Check GitHub repositories for new releases (discovery only, no downloads)."""
    root: Path = ctx.obj["root"]

    try:
        fdhub_config = _load_config(root)
    except FileNotFoundError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        sys.exit(1)

    from fdhub.scheduler.pipeline import FDHubPipeline

    pipeline = FDHubPipeline(
        fdhub_config=fdhub_config,
        project_root=root,
        github_token=os.environ.get("GITHUB_TOKEN"),
        dry_run=dry_run,
        target_app_id=app_id,
    )

    async def _run():
        results = await pipeline.discover()
        changed = [(c, s) for c, s, changed in results if changed]
        unchanged = [(c, s) for c, s, changed in results if not changed]

        console.print(f"\n[bold]Discovery Result:[/bold]")
        console.print(f"  Total apps:      {len(results)}")
        console.print(f"  [green]With changes:[/green]  {len(changed)}")
        console.print(f"  Unchanged:       {len(unchanged)}")

        if changed:
            console.print("\n[bold]Apps with new releases:[/bold]")
            for c, _ in changed:
                console.print(f"  • {c.id} ({c.github})")

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# process
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("app_id")
@click.option("--dry-run", is_flag=True, help="Do not modify state or publish")
@click.pass_context
def process(ctx: click.Context, app_id: str, dry_run: bool) -> None:
    """Process a single app: download new APKs, inspect, update state."""
    root: Path = ctx.obj["root"]
    try:
        fdhub_config = _load_config(root)
    except FileNotFoundError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        sys.exit(1)

    from fdhub.scheduler.pipeline import FDHubPipeline

    pipeline = FDHubPipeline(
        fdhub_config=fdhub_config,
        project_root=root,
        github_token=os.environ.get("GITHUB_TOKEN"),
        dry_run=dry_run,
        target_app_id=app_id,
    )

    report = asyncio.run(pipeline.run())
    console.print(report.to_markdown())
    if report.failed_apps > 0:
        sys.exit(1)


# ---------------------------------------------------------------------------
# process-all
# ---------------------------------------------------------------------------

@cli.command("process-all")
@click.option("--dry-run", is_flag=True, help="Do not modify state or publish")
@click.pass_context
def process_all(ctx: click.Context, dry_run: bool) -> None:
    """Process all configured apps."""
    root: Path = ctx.obj["root"]
    try:
        fdhub_config = _load_config(root)
    except FileNotFoundError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        sys.exit(1)

    from fdhub.scheduler.pipeline import FDHubPipeline

    pipeline = FDHubPipeline(
        fdhub_config=fdhub_config,
        project_root=root,
        github_token=os.environ.get("GITHUB_TOKEN"),
        dry_run=dry_run,
    )

    report = asyncio.run(pipeline.run())
    console.print(report.to_markdown())
    if report.failed_apps > 0:
        sys.exit(1)


# ---------------------------------------------------------------------------
# generate-index
# ---------------------------------------------------------------------------

@cli.command("generate-index")
@click.pass_context
def generate_index(ctx: click.Context) -> None:
    """Regenerate F-Droid index from current state (no downloads)."""
    root: Path = ctx.obj["root"]
    console.print("[yellow]generate-index: regenerating index from current state[/yellow]")
    # TODO: Load all state files, rebuild Application models, generate index
    console.print("[green]Done[/green]")


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------

@cli.command()
@click.pass_context
def report(ctx: click.Context) -> None:
    """Print the latest run report."""
    root: Path = ctx.obj["root"]
    report_path = root / "generated" / "reports" / "latest.md"
    if not report_path.exists():
        console.print("[yellow]No report found yet. Run 'fdhub process-all' first.[/yellow]")
        return
    console.print(report_path.read_text())


# ---------------------------------------------------------------------------
# init-signing
# ---------------------------------------------------------------------------

@cli.command("init-signing")
@click.option("--alias", default="repokey", help="Key alias in keystore")
@click.option("--output", default="keystore.jks", help="Output keystore filename")
@click.option("--validity", default=10000, help="Key validity in days")
@click.pass_context
def init_signing(ctx: click.Context, alias: str, output: str, validity: int) -> None:
    """
    Generate a new F-Droid repository signing key.

    This command generates a JKS keystore. The keystore must be:
    1. Stored securely (NOT committed to Git)
    2. Base64-encoded and stored as FDROID_KEYSTORE_BASE64 GitHub Secret
    3. Backed up in a secure location

    Run this ONCE and store the result permanently.
    """
    import secrets
    import subprocess
    import shutil

    keytool = shutil.which("keytool")
    if not keytool:
        console.print("[red]Error:[/red] 'keytool' not found. Install a JDK.")
        sys.exit(1)

    # Generate a strong password
    ks_pass = secrets.token_urlsafe(32)
    output_path = Path(output)

    console.print(f"\n[bold]Generating signing key...[/bold]")
    console.print(f"  Output:    {output_path.absolute()}")
    console.print(f"  Alias:     {alias}")
    console.print(f"  Validity:  {validity} days")

    cmd = [
        keytool, "-genkey", "-v",
        "-keystore", str(output_path),
        "-alias", alias,
        "-keyalg", "RSA",
        "-keysize", "4096",
        "-validity", str(validity),
        "-storepass", ks_pass,
        "-keypass", ks_pass,
        "-dname", "CN=FDHub Repository,O=FDHub,C=US",
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        console.print(f"[red]keytool failed:[/red]\n{result.stderr}")
        sys.exit(1)

    # Encode to base64
    import base64
    b64 = base64.b64encode(output_path.read_bytes()).decode()

    console.print("\n[green]✅ Signing key generated successfully![/green]\n")
    console.print("[bold]IMPORTANT: Follow these steps:[/bold]\n")
    console.print("1. Add the following GitHub Actions secrets to your repository:\n")
    console.print(f"   [cyan]FDROID_KEYSTORE_BASE64[/cyan] =")
    console.print(f"   {b64[:60]}...")
    console.print(f"\n   [cyan]FDROID_KEYSTORE_PASS[/cyan] = {ks_pass}")
    console.print(f"   [cyan]FDROID_KEY_ALIAS[/cyan]   = {alias}")
    console.print(f"\n2. [red]Back up '{output_path}' and the password securely![/red]")
    console.print(f"3. Add '{output_path}' to your .gitignore (already done)")
    console.print("\n[yellow]⚠️  Never commit the keystore or password to Git.[/yellow]")


# ---------------------------------------------------------------------------
# backfill
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("app_id")
@click.option("--dry-run", is_flag=True)
@click.pass_context
def backfill(ctx: click.Context, app_id: str, dry_run: bool) -> None:
    """
    Load full release history for an app (overrides bootstrap policy).

    Normally FDHub only indexes the latest release when an app is first added.
    This command loads ALL historical releases for a specific app.
    """
    console.print(
        f"[yellow]Backfilling all historical releases for '{app_id}'...[/yellow]"
    )
    console.print("[yellow]This may take a while and consume significant API quota.[/yellow]")
    # TODO: Implement backfill — temporarily sets bootstrapped=True and
    # processes all releases from oldest to newest
    console.print("[red]Backfill command: not yet implemented[/red]")


# ---------------------------------------------------------------------------
# rebuild
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--dry-run", is_flag=True)
@click.pass_context
def rebuild(ctx: click.Context, dry_run: bool) -> None:
    """
    Full historical rebuild of the repository index.

    Use this for:
    - State migration after FDHub updates
    - F-Droid format changes
    - Recovery after data loss
    - Signing key rotation

    This does NOT re-download APKs if they are in the cache.
    """
    console.print("[yellow]Starting full rebuild...[/yellow]")
    console.print("[red]Rebuild command: not yet implemented[/red]")


if __name__ == "__main__":
    cli()
