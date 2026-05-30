from typing import Protocol, runtime_checkable


@runtime_checkable
class IRetriever(Protocol):
    def search(
        self, query: str, top_k: int, filters: dict | None = None
    ) -> list[dict]:
        ...
