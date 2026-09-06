from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Sequence
from pathlib import Path

from examples.talk_to_data_multi_agent.application.catalog import load_schema_catalog
from examples.talk_to_data_multi_agent.application.workflow import TalkToDataWorkflow
from traccia_runtime.config.settings import load_settings
from traccia_runtime.orchestration.container import build_container
from traccia_runtime.runtime.client import TracciaRuntimeClient

EXAMPLE_DIR = Path(__file__).resolve().parent
CONFIG_DIR = EXAMPLE_DIR / "config"
DATA_DIR = EXAMPLE_DIR / "data"
DOCS_DIR = EXAMPLE_DIR / "docs"
UI_DIR = EXAMPLE_DIR / "ui"
CONFIG_PATH = CONFIG_DIR / "agent.yaml"
SEMANTIC_LAYER_PATH = CONFIG_DIR / "semantic_layer.yaml"
DEFAULT_SCHEMA = DATA_DIR / "schema" / "database_context.md"
DASHBOARD_UI_PATH = UI_DIR / "dashboard.html"
ARCHITECTURE_V1_DIR = DOCS_DIR / "architecture" / "v1"
ARCHITECTURE_V2_DIR = DOCS_DIR / "architecture" / "v2"
TENANT_ID = "talk-to-data-example"


async def _ask_human(question: str) -> str:
    return await asyncio.to_thread(input, f"\nNeed one clarification: {question}\n> ")


async def run(
    question: str | None,
    schema_path: Path,
    index: bool,
    max_clarifications: int,
) -> None:
    catalog = load_schema_catalog(schema_path, TENANT_ID)
    container = build_container(load_settings((CONFIG_PATH,)))
    try:
        await container.astart()
        if index:
            count = await container.retrievers.get("database_schema").ingest(catalog.documents)
            print(f"Indexed {count} schema documentation chunks from {len(catalog.tables)} tables.")
        if question:
            workflow = TalkToDataWorkflow(
                TracciaRuntimeClient(container.runtime),
                catalog,
                tenant_id=TENANT_ID,
                user_id="cli-user",
                max_clarifications=max_clarifications,
            )
            result = await workflow.run(question, _ask_human)
            print("\nFinal PostgreSQL:\n")
            print(result.sql)
            if result.parameters:
                print(
                    "\nParameters:",
                    json.dumps(
                        [parameter.model_dump(mode="json") for parameter in result.parameters],
                        indent=2,
                    ),
                )
            if result.assumptions:
                print("\nAssumptions:")
                for assumption in result.assumptions:
                    print(f"- {assumption}")
        if not index and not question:
            raise SystemExit("provide a question, --index, or both")
    finally:
        await container.aclose()


def parse_args(arguments: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Multi-agent natural-language-to-PostgreSQL workflow."
    )
    parser.add_argument("question", nargs="?")
    parser.add_argument("--index", action="store_true", help="Index or replace schema documents.")
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--max-clarifications", type=int, default=2, choices=range(0, 6))
    return parser.parse_args(arguments)


def main() -> None:
    arguments = parse_args()
    asyncio.run(
        run(
            arguments.question,
            arguments.schema,
            arguments.index,
            arguments.max_clarifications,
        )
    )


if __name__ == "__main__":
    main()
