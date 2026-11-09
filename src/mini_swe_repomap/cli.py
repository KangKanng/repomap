import os
from importlib.resources import files
from pathlib import Path
from typing import Annotated

import typer

app = typer.Typer(help="Preview repository maps or locate mini-swe-agent config overlays.", no_args_is_help=True)


@app.command()
def show(
    root: Annotated[Path, typer.Argument(help="Repository to map.")] = Path("."),
    task: Annotated[str, typer.Option("--task", "-t", help="Task used to rank definitions.")] = "",
    tokens: Annotated[int, typer.Option("--tokens", min=0)] = 1024,
    tokenizer: Annotated[str, typer.Option("--tokenizer")] = "cl100k_base",
    full: Annotated[
        bool, typer.Option("--full", help="Include all collected paths and definitions, without scan or token caps.")
    ] = False,
):
    """Print the same map that the agent receives, without calling an LLM."""
    os.environ.setdefault("MSWEA_SILENT_STARTUP", "1")
    from minisweagent.config import get_config_from_spec
    from minisweagent.environments.local import LocalEnvironment

    from mini_swe_repomap.repomap import RepoMap
    from mini_swe_repomap.settings import RepoMapConfig
    from mini_swe_repomap.source import EnvironmentSource

    if tokens == 0:
        return
    options = (
        get_config_from_spec(files("mini_swe_repomap").joinpath("config", "full-once.yaml"))["agent"]["repomap"]
        if full
        else {}
    )
    settings = RepoMapConfig(**(options | {"root": str(root), "map_tokens": tokens}))
    source = EnvironmentSource(LocalEnvironment(), settings)
    typer.echo(RepoMap(tokenizer).build(source.read(), task, tokens, mode=settings.mode))
    for warning in source.warnings:
        typer.echo(warning, err=True)


@app.command()
def config(
    batch: Annotated[bool, typer.Option("--batch", help="Use the non-interactive agent, e.g. for SWE-bench.")] = False,
    full_once: Annotated[
        bool, typer.Option("--full-once", help="Print the full-map-once overlay; apply after the agent overlay.")
    ] = False,
):
    """Print the installed overlay path for mini -c mini.yaml -c PATH."""
    if batch and full_once:
        raise typer.BadParameter("Use config --batch and config --full-once as separate overlays.")
    name = "full-once.yaml" if full_once else "batch.yaml" if batch else "interactive.yaml"
    typer.echo(str(files("mini_swe_repomap").joinpath("config", name)))
