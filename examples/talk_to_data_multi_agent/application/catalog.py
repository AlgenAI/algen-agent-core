from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from traccia_runtime.retrieval.contracts import SourceDocument

_SECTION = re.compile(r"(?m)^##\s+(.+?)\s*$")
_TABLE_HEADING = re.compile(
    r"(?m)^#{2,3}\s+.*?`(?:[a-zA-Z_][a-zA-Z0-9_]*\.)?"
    r"([a-zA-Z_][a-zA-Z0-9_]*)`"
)
_QUALIFIED_TABLE = re.compile(r"`public\.([a-zA-Z_][a-zA-Z0-9_]*)`")


@dataclass(frozen=True)
class SchemaCatalog:
    documents: tuple[SourceDocument, ...]
    tables: frozenset[str]


def load_schema_catalog(path: str | Path, tenant_id: str) -> SchemaCatalog:
    """Turn Markdown sections into independently retrievable, untrusted schema documents."""
    source_path = Path(path)
    text = source_path.read_text(encoding="utf-8")
    matches = list(_SECTION.finditer(text))
    if not matches:
        raise ValueError("schema Markdown must contain at least one level-two heading")

    documents: list[SourceDocument] = []
    tables: set[str] = set()
    for index, match in enumerate(matches):
        title = match.group(1).strip()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        section = text[match.start() : end].strip()
        section_tables = tuple(
            dict.fromkeys(
                (*_TABLE_HEADING.findall(section), *_QUALIFIED_TABLE.findall(section))
            )
        )
        tables.update(section_tables)
        slug_source = "-".join(section_tables) if section_tables else re.sub(
            r"[^a-z0-9]+", "-", title.lower()
        ).strip("-")
        documents.append(
            SourceDocument(
                id=f"schema-{slug_source or index}",
                text=section,
                source=f"schema://postgres/{slug_source or index}",
                title=title,
                uri=source_path.resolve().as_uri(),
                metadata={
                    "tenant_id": tenant_id,
                    "knowledge_type": "postgres_schema",
                    "tables": ",".join(section_tables),
                    "untrusted_content": True,
                },
            )
        )
    if not tables:
        raise ValueError("schema Markdown does not identify any backtick-delimited tables")
    return SchemaCatalog(tuple(documents), frozenset(tables))
