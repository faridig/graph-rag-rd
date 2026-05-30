import os

from dotenv import load_dotenv

load_dotenv()

NEO4J_URI: str = os.environ["NEO4J_URI"]
NEO4J_USER: str = os.environ["NEO4J_USER"]
NEO4J_PASSWORD: str = os.environ["NEO4J_PASSWORD"]
OPENAI_API_KEY: str = os.environ["OPENAI_API_KEY"]
ANTHROPIC_API_KEY: str = os.environ["ANTHROPIC_API_KEY"]

EMBEDDING_MODEL: str = "text-embedding-3-large"
EMBEDDING_DIMS: int = 1536

LLM_MODEL: str = "claude-sonnet-4-6"

TOP_K_DEFAULT: int = 10

CORPUS_SCOPE: list[str] = ["REPERTOIRE-RD-2025-2026", "ACE-3", "ACE-5"]

FALLBACK_MESSAGE: str = (
    "Information absente du corpus actuel.\n"
    "Sources indexées : REPERTOIRE-RD-2025-2026 (316 runs), ACE-3, ACE-5.\n"
    "Les autres fiches d'essai ne sont pas encore intégrées."
)

# Calibré empiriquement (calibrate_threshold.py) :
# présentes min=0.7159/max=0.8333 | absentes min=0.6422/max=0.7517
SCORE_THRESHOLD: float = 0.7338
