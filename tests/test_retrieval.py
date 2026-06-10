"""Unit tests for retrieval helpers — Neo4j driver mocked."""

from __future__ import annotations

from unittest.mock import MagicMock

from src.retrieval.exact_lookup import exact_lookup


class _FakeRecord:
    """Minimal mapping compatible with dict() conversion."""

    def __init__(self, data: dict) -> None:
        self._data = data

    def keys(self) -> object:
        return self._data.keys()

    def __getitem__(self, key: str) -> object:
        return self._data[key]


def _make_driver(rows: list[dict]) -> MagicMock:
    mock_driver = MagicMock()
    mock_session = MagicMock()
    mock_driver.session.return_value.__enter__.return_value = mock_session
    mock_session.run.return_value = [_FakeRecord(r) for r in rows]
    return mock_driver


# ── exact_lookup ──────────────────────────────────────────────────────────────

_FOUND_ROW = {
    "run_id": "ACE-3:Run:1",
    "experiment_id": "ACE-3",
    "objective": "Test Nutralys",
    "synthesis": "Bons résultats",
    "date": None,
    "chantier": None,
    "ingredient_match": "Nutralys S85F",
}


def test_exact_lookup_returns_rows_when_ingredient_found():
    driver = _make_driver([_FOUND_ROW])
    result = exact_lookup(driver, "Nutralys S85F")
    assert len(result) == 1
    assert result[0]["run_id"] == "ACE-3:Run:1"
    assert result[0]["ingredient_match"] == "Nutralys S85F"


def test_exact_lookup_returns_empty_list_when_not_found():
    driver = _make_driver([])
    result = exact_lookup(driver, "IngredientInexistant999")
    assert result == []


def test_exact_lookup_passes_name_to_cypher():
    driver = _make_driver([])
    exact_lookup(driver, "Pisane ES")
    # exact_lookup now makes two calls: ingredient CONTAINS + fulltext.
    # Verify the ingredient query (first call) passes the name kwarg.
    all_calls = driver.session.return_value.__enter__.return_value.run.call_args_list
    first_call_kwargs = all_calls[0][1] if all_calls else {}
    assert first_call_kwargs.get("name") == "Pisane ES"


def test_exact_lookup_returns_all_rows():
    rows = [_FOUND_ROW, {**_FOUND_ROW, "run_id": "ACE-3:Run:2"}]
    driver = _make_driver(rows)
    result = exact_lookup(driver, "Nutralys")
    assert len(result) == 2
