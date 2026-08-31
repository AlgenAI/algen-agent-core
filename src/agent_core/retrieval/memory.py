from __future__ import annotations

import re

from agent_core.retrieval.contracts import RetrievalQuery, RetrievedDocument


class InMemoryRetriever:
    def __init__(self, documents: tuple[RetrievedDocument, ...] = ()) -> None:
        self._documents = list(documents)

    def add(self, document: RetrievedDocument) -> None:
        self._documents.append(document)

    async def retrieve(self, query: RetrievalQuery) -> tuple[RetrievedDocument, ...]:
        terms = set(re.findall(r"\w+", query.text.lower()))
        results: list[RetrievedDocument] = []
        for document in self._documents:
            if document.metadata.get("tenant_id") not in {None, query.tenant_id}:
                continue
            if any(document.metadata.get(key) != value for key, value in query.filters.items()):
                continue
            words = set(re.findall(r"\w+", document.text.lower()))
            score = len(terms & words) / max(1, len(terms | words))
            if score:
                results.append(document.model_copy(update={"score": score}))
        return tuple(sorted(results, key=lambda item: item.score, reverse=True)[: query.limit])

