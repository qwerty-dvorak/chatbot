#!/usr/bin/env python3
"""CLI entry point for document ingestion."""

import sys

import click

from pipeline.ingest import ingest_path


@click.command()
@click.argument("path", type=click.Path(exists=True))
@click.option(
    "--strategy",
    "-s",
    type=click.Choice(["recursive", "sentence_window", "hierarchical"]),
    default=None,
    help="Chunking strategy (overrides CHUNK_STRATEGY env var).",
)
@click.option(
    "--hypothetical-questions", "-q", is_flag=True, default=False, help="Generate hypothetical questions per chunk at index time (requires LLM)."
)
def main(path: str, strategy: str | None, hypothetical_questions: bool) -> None:  # noqa: FBT001
    """
    Ingest documents from PATH into Milvus + BM25 index.

    PATH may be a single file or a directory (processed recursively).
    Supported formats: .txt .md .rst .pdf .png .jpg .jpeg .bmp .webp .gif
    """
    click.echo(f"Ingesting: {path}")
    if strategy:
        click.echo(f"Strategy:  {strategy}")
    if hypothetical_questions:
        click.echo("Hypothetical questions: enabled (index-time LLM augmentation)")

    try:
        stats = ingest_path(path, strategy=strategy, add_hypothetical_questions=hypothetical_questions)
    except Exception as exc:  # noqa: BLE001
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)

    click.echo(
        f"\nDone — files: {stats['files_processed']}, chunks: {stats['chunks_created']}, embeddings: {stats['embeddings_indexed']}",
    )


if __name__ == "__main__":
    main()
