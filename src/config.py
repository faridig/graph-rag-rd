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

TOP_K_DEFAULT: int = 6

CORPUS_SCOPE: list[str] = []

# Chemin vers la liste des sujets absents du corpus (rechargé périodiquement).
ABSENT_TOPICS_PATH: str = os.path.join(
    os.path.dirname(__file__), "..", "data", "absent_topics.txt"
)

# TTL en secondes pour le rechargement des sets d'IDs Neo4j et du fichier absent_topics.
# Valeur 0 = rechargement à chaque requête (désactiver en prod si Neo4j est lent).
RAG_IDS_CACHE_TTL: int = int(os.getenv("RAG_IDS_CACHE_TTL", "300"))

FALLBACK_MESSAGE: str = "Information absente du corpus actuel."

# Calibré empiriquement (calibrate_threshold.py) :
# présentes min=0.6789/max=0.8310 | absentes min=0.6843/max=0.8122
SCORE_THRESHOLD: float = 0.6689
