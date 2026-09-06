from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence
from pathlib import Path

from examples.text_to_sql_agent.app import ReadOnlyAnalyticsDatabase, create_query_tool
from examples.text_to_sql_pgvector_agent.knowledge import TENANT_ID, schema_documents
from traccia_runtime.config.settings import load_settings
from traccia_runtime.orchestration.container import build_container
from traccia_runtime.runtime.client import TracciaRuntimeClient
from traccia_runtime.types.contracts import RunRequest

EXAMPLE_DIR = Path(__file__).resolve().parent


async def index_schema() -> int:
    container = build_container(load_settings((EXAMPLE_DIR / "agent.yaml",)))
    try:
        return await container.retrievers.get("analytics_schema").ingest(schema_documents())
    finally:
        await container.aclose()


async def ask(question: str) -> str:
    container = build_container(load_settings((EXAMPLE_DIR / "agent.yaml",)))
    database = ReadOnlyAnalyticsDatabase()
    container.tools.register(create_query_tool(database))
    try:
        result = await TracciaRuntimeClient(container.runtime).run(
            RunRequest(
                agent="text-to-sql-pgvector",
                input=question,
                tenant_id=TENANT_ID,
                user_id="example-user",
            )
        )
    finally:
        database.close()
        await container.aclose()
    if result.error:
        raise RuntimeError(result.error)
    return result.output or ""


def parse_args(arguments: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Text-to-SQL using OpenAI and schema context stored in pgvector."
    )
    parser.add_argument("question", nargs="?")
    parser.add_argument(
        "--index",
        action="store_true",
        help="Create/update the pgvector schema knowledge before querying.",
    )
    return parser.parse_args(arguments)


async def _main(arguments: argparse.Namespace) -> None:
    if arguments.index:
        count = await index_schema()
        print(f"Indexed {count} schema knowledge chunks.")
    if arguments.question:
        print(await ask(arguments.question))
    if not arguments.index and not arguments.question:
        raise SystemExit("provide a question, --index, or both")


def main() -> None:
    asyncio.run(_main(parse_args()))


if __name__ == "__main__":
    main()
