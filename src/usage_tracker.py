"""Suivi budgétaire des appels API Claude — persisté dans data/usage.json."""

from __future__ import annotations

import json
import logging
import threading
from datetime import date
from pathlib import Path

from src.config import DAILY_BUDGET_EUR, MONTHLY_BUDGET_EUR, USD_TO_EUR

_log = logging.getLogger(__name__)

# Coûts Claude Sonnet 4.6 (USD par token)
_INPUT_COST_PER_TOKEN_USD: float = 3.0 / 1_000_000
_OUTPUT_COST_PER_TOKEN_USD: float = 15.0 / 1_000_000

_USAGE_FILE = Path(__file__).parent.parent / "data" / "usage.json"
_lock = threading.Lock()


def _load() -> dict:
    try:
        with open(_USAGE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"daily": {}, "monthly": {}}


def _save(data: dict) -> None:
    _USAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(_USAGE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def record_usage(input_tokens: int, output_tokens: int) -> float:
    """Enregistre une consommation et retourne le coût ajouté (EUR)."""
    cost_usd = (
        input_tokens * _INPUT_COST_PER_TOKEN_USD
        + output_tokens * _OUTPUT_COST_PER_TOKEN_USD
    )
    cost_eur = cost_usd * USD_TO_EUR
    today = date.today()
    day_key = today.isoformat()          # "2026-06-23"
    month_key = today.strftime("%Y-%m")  # "2026-06"

    with _lock:
        data = _load()
        for bucket, key in [("daily", day_key), ("monthly", month_key)]:
            entry = data[bucket].setdefault(
                key,
                {"input_tokens": 0, "output_tokens": 0, "cost_eur": 0.0, "queries": 0},
            )
            entry["input_tokens"] += input_tokens
            entry["output_tokens"] += output_tokens
            entry["cost_eur"] += cost_eur
            entry["queries"] += 1
        _save(data)

    _log.info(
        "Usage recorded: +%d in / +%d out / +€%.4f",
        input_tokens, output_tokens, cost_eur,
    )
    return cost_eur


def get_daily_total() -> float:
    day_key = date.today().isoformat()
    with _lock:
        data = _load()
    return data["daily"].get(day_key, {}).get("cost_eur", 0.0)


def get_monthly_total() -> float:
    month_key = date.today().strftime("%Y-%m")
    with _lock:
        data = _load()
    return data["monthly"].get(month_key, {}).get("cost_eur", 0.0)


def check_budget() -> tuple[bool, str]:
    """Retourne (autorisé, message_erreur). Si autorisé → message vide."""
    daily = get_daily_total()
    monthly = get_monthly_total()

    if DAILY_BUDGET_EUR > 0 and daily >= DAILY_BUDGET_EUR:
        return False, (
            f"Budget journalier atteint ({daily:.2f} € / {DAILY_BUDGET_EUR:.2f} €). "
            "Réessayez demain ou contactez l'administrateur."
        )
    if MONTHLY_BUDGET_EUR > 0 and monthly >= MONTHLY_BUDGET_EUR:
        return False, (
            f"Budget mensuel atteint ({monthly:.2f} € / {MONTHLY_BUDGET_EUR:.2f} €). "
            "Contactez l'administrateur."
        )
    return True, ""


def usage_summary() -> str:
    """Résumé lisible pour affichage dans le chat."""
    daily = get_daily_total()
    monthly = get_monthly_total()
    day_str = f"Aujourd'hui : {daily:.3f} €"
    if DAILY_BUDGET_EUR > 0:
        day_str += f" / {DAILY_BUDGET_EUR:.2f} €"
    month_str = f"Ce mois : {monthly:.3f} €"
    if MONTHLY_BUDGET_EUR > 0:
        month_str += f" / {MONTHLY_BUDGET_EUR:.2f} €"
    return f"{day_str} | {month_str}"
