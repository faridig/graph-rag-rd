#!/usr/bin/env bash
# Setup Neo4j on sandbox: start, load dump, fix .env
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
DUMP_FILE="$PROJECT_DIR/neo4j_dump/neo4j.dump"
ENV_FILE="$PROJECT_DIR/.env"

cd "$PROJECT_DIR"

# ── 1. Vérifications préalables ──────────────────────────────────────────────
if [ ! -f "$DUMP_FILE" ]; then
  echo "ERREUR : dump introuvable à $DUMP_FILE"
  exit 1
fi

if [ ! -f "$ENV_FILE" ]; then
  echo "ERREUR : .env introuvable à $ENV_FILE"
  exit 1
fi

echo "==> [1/5] Démarrage Neo4j..."
docker compose up -d neo4j

# ── 2. Attendre que Neo4j soit healthy ───────────────────────────────────────
echo "==> [2/5] Attente Neo4j healthy (max 120s)..."
NEO4J_CONTAINER=$(docker compose ps -q neo4j)
for i in $(seq 1 24); do
  STATUS=$(docker inspect --format='{{.State.Health.Status}}' "$NEO4J_CONTAINER" 2>/dev/null || echo "starting")
  if [ "$STATUS" = "healthy" ]; then
    echo "    Neo4j healthy."
    break
  fi
  if [ "$i" -eq 24 ]; then
    echo "ERREUR : Neo4j n'est pas healthy après 120s"
    docker compose logs neo4j | tail -20
    exit 1
  fi
  echo "    ($i/24) status=$STATUS — attente 5s..."
  sleep 5
done

# ── 3. Arrêter Neo4j ────────────────────────────────────────────────────────
echo "==> [3/5] Arrêt Neo4j avant chargement du dump..."
docker compose stop neo4j

# ── 4. Charger le dump via volume monté ─────────────────────────────────────
echo "==> [4/5] Chargement du dump (volume monté)..."
DUMP_DIR="$(dirname "$DUMP_FILE")"

# neo4j-admin database load nécessite que Neo4j soit arrêté.
# On monte le dossier du dump directement — docker cp ne fonctionne pas
# avec docker compose run --rm car c'est un container différent.
docker compose run --rm \
  -v "${DUMP_DIR}:/dumps" \
  neo4j \
  neo4j-admin database load neo4j \
  --from-path=/dumps \
  --overwrite-destination=true

echo "==> Redémarrage Neo4j..."
docker compose up -d neo4j

# Attendre que Neo4j soit healthy après le load
echo "    Attente Neo4j healthy post-load (max 120s)..."
NEO4J_CONTAINER=$(docker compose ps -q neo4j)
for i in $(seq 1 24); do
  STATUS=$(docker inspect --format='{{.State.Health.Status}}' "$NEO4J_CONTAINER" 2>/dev/null || echo "starting")
  if [ "$STATUS" = "healthy" ]; then
    echo "    Neo4j healthy."
    break
  fi
  if [ "$i" -eq 24 ]; then
    echo "ERREUR : Neo4j n'est pas healthy après le load"
    docker compose logs neo4j | tail -20
    exit 1
  fi
  echo "    ($i/24) status=$STATUS — attente 5s..."
  sleep 5
done

# ── 5. Corriger NEO4J_URI dans .env ─────────────────────────────────────────
echo "==> [5/5] Mise à jour NEO4J_URI dans .env (port 7688)..."
if grep -q "^NEO4J_URI=" "$ENV_FILE"; then
  sed -i 's|^NEO4J_URI=.*|NEO4J_URI=bolt://localhost:7688|' "$ENV_FILE"
  echo "    NEO4J_URI mis à jour."
else
  echo "NEO4J_URI=bolt://localhost:7688" >> "$ENV_FILE"
  echo "    NEO4J_URI ajouté."
fi

# ── Résumé ───────────────────────────────────────────────────────────────────
echo ""
echo "✓ Neo4j prêt sur bolt://localhost:7688 (HTTP: http://localhost:7475)"
echo ""
echo "Prochaine étape — lancer l'appli :"
echo "  cd $PROJECT_DIR"
echo "  PYTHONPATH=. chainlit run src/chainlit_app.py --port 8001"
