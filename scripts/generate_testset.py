"""Generate a synthetic gold testset from _documentation.md files using Claude Haiku.

Reads documentation files, samples by experiment family (max 2 per family),
and asks Claude to generate 3 (question, ground_truth) pairs per file.

Usage:
    python scripts/generate_testset.py                      # 20 files → data/testset.json
    python scripts/generate_testset.py --n 30
    python scripts/generate_testset.py --dry-run            # list files without API calls
    python scripts/generate_testset.py --seed 123           # reproducible sampling
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
from pathlib import Path

import anthropic

from src.config import ANTHROPIC_API_KEY

_SYSTEM = (
    "Tu es expert R&D en analogues de viande végétaux HME (extrusion haute humidité).\n"
    "Tu génères des questions-réponses pour évaluer un système RAG interne.\n\n"
    "À partir du rapport fourni, génère EXACTEMENT 3 objets JSON dans un tableau :\n"
    '[\n  {"question": "...", "ground_truth": "...", "type": "factuelle"},\n'
    '  {"question": "...", "ground_truth": "...", "type": "synthèse"},\n'
    '  {"question": "...", "ground_truth": "...", "type": "comparative"}\n]\n\n'
    "Règles :\n"
    "- factuelle   : un fait précis (chiffre, formulation, résultat mesurable, identifiant run)\n"
    "- synthèse    : conclusions générales en intégrant plusieurs runs\n"
    "- comparative : compare deux conditions ou deux runs explicitement\n"
    "- La ground_truth est basée UNIQUEMENT sur le rapport — aucun fait inventé\n"
    "- Inclure les valeurs numériques et noms de runs quand disponibles\n"
    "- Répondre UNIQUEMENT avec le tableau JSON valide, aucun texte autour"
)

_MAX_DOC_CHARS = 12_000
_JSON_ARRAY_RE = re.compile(r"\[.*?\]", re.DOTALL)


def _experiment_family(stem: str) -> str:
    """Extract family prefix: ACE-4 → ACE, MDD-EMINCE-THAI → MDD, etc."""
    m = re.match(r"^([A-Z][A-Z0-9]*(?:-[A-Z]+)*?)(?:-?\d|$)", stem.upper())
    return m.group(1).rstrip("-") if m else stem[:8]


def _sample_files(data_root: Path, n: int) -> list[Path]:
    all_docs = sorted(
        f for f in data_root.rglob("*_documentation.md") if "REPERTOIRE" not in f.stem
    )
    families: dict[str, list[Path]] = {}
    for f in all_docs:
        stem = f.stem.replace("_documentation", "")
        fam = _experiment_family(stem)
        families.setdefault(fam, []).append(f)

    candidates: list[Path] = []
    for fam_files in families.values():
        random.shuffle(fam_files)
        candidates.extend(fam_files[:2])

    random.shuffle(candidates)
    return candidates[:n]


def _generate_pairs(client: anthropic.Anthropic, doc_path: Path) -> list[dict]:
    exp_id = doc_path.stem.replace("_documentation", "")
    content = doc_path.read_text(encoding="utf-8")[:_MAX_DOC_CHARS]

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2048,
        system=_SYSTEM,
        messages=[{"role": "user", "content": f"Rapport d'essai :\n\n{content}"}],
    )
    raw = response.content[0].text.strip()

    m = _JSON_ARRAY_RE.search(raw)
    if not m:
        print(f"  [WARN] JSON non trouvé pour {exp_id}: {raw[:80]!r}", file=sys.stderr)
        return []

    try:
        pairs = json.loads(m.group(0))
    except json.JSONDecodeError as exc:
        print(f"  [WARN] JSON invalide pour {exp_id}: {exc}", file=sys.stderr)
        return []

    valid = []
    for p in pairs:
        if isinstance(p, dict) and p.get("question") and p.get("ground_truth"):
            p["experiment_id"] = exp_id
            valid.append(p)
    return valid


def main() -> None:
    parser = argparse.ArgumentParser(description="Génère un gold testset depuis les docs R&D")
    parser.add_argument(
        "--n", type=int, default=20, metavar="N", help="Nb de fichiers (défaut: 20)"
    )
    parser.add_argument("--out", default="data/testset.json", help="Chemin de sortie")
    parser.add_argument(
        "--dry-run", action="store_true", help="Lister les fichiers sans appels API"
    )
    parser.add_argument("--seed", type=int, default=42, help="Graine aléatoire (défaut: 42)")
    args = parser.parse_args()

    random.seed(args.seed)
    data_root = Path("data")
    files = _sample_files(data_root, args.n)

    print(f"Fichiers sélectionnés ({len(files)}) :")
    for f in files:
        print(f"  {f.stem.replace('_documentation', '')}")

    if args.dry_run:
        print("\n[dry-run] Aucun appel API.")
        return

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    all_pairs: list[dict] = []

    for i, f in enumerate(files, 1):
        exp_id = f.stem.replace("_documentation", "")
        print(f"[{i}/{len(files)}] {exp_id}…", end=" ", flush=True)
        try:
            pairs = _generate_pairs(client, f)
            all_pairs.extend(pairs)
            print(f"{len(pairs)} paires")
        except Exception as exc:
            print(f"ERREUR: {exc}", file=sys.stderr)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(all_pairs, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n✓ {len(all_pairs)} paires → {out_path}")
    cost_est = len(files) * 0.015  # ~$0.015 par fichier avec Sonnet 4.6
    print(f"  Coût estimé : ~${cost_est:.2f}")


if __name__ == "__main__":
    main()
