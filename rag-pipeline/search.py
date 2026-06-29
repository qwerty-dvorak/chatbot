#!/usr/bin/env python3
"""CLI entry point for RAG search."""

import os
import sys

import click

from pipeline.config import cfg
from pipeline.search import format_results, search


@click.command()
@click.argument("query")
@click.option("--top-k", "-k", default=None, type=int, help="Number of results to return (default: RERANK_TOP_K env var).")
@click.option("--mode", "-m", type=click.Choice(["hybrid", "vector", "bm25"]), default="hybrid", help="Retrieval mode.")
@click.option("--no-rerank", is_flag=True, default=False, help="Skip reranker step.")
@click.option(
    "--enhancements", "-e", default=None, help="Comma-separated query enhancements: hyde,sub_queries,stepback (overrides QUERY_ENHANCEMENTS env var)."
)
def main(query: str, top_k: int | None, mode: str, no_rerank: bool, enhancements: str | None) -> None:  # noqa: FBT001
    """
    Search the RAG index for QUERY.

    Examples:
    \b
      uv run python search.py "what is milvus?"
      uv run python search.py "explain chunking" --mode vector --no-rerank
      uv run python search.py "complex question" --enhancements hyde,sub_queries

    """  # noqa: D301
    if enhancements is not None:
        os.environ["QUERY_ENHANCEMENTS"] = enhancements
        cfg.query_enhancements = enhancements

    click.echo(f"Query: {query!r}  mode={mode}  rerank={not no_rerank}\n")

    try:
        results = search(
            query,
            top_k=top_k,
            use_reranker=not no_rerank,
            retrieval_mode=mode,
        )
    except Exception as exc:  # noqa: BLE001
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)

    if not results:
        click.echo("No results found.")
        return

    click.echo(format_results(results))


if __name__ == "__main__":
    main()
