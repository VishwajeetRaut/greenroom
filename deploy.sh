#!/bin/bash
# One-command deploy: builds all three Docker images, pushes to GitHub Container Registry,
# and updates the Azure Container Apps in Sweden Central.
#
# Prerequisites (one-time):
#   1. az login  (already done)
#   2. docker login ghcr.io -u <your-github-username> -p <github-personal-access-token>
#      Get a token at: GitHub → Settings → Developer settings → Personal access tokens
#      Scopes needed: write:packages, read:packages
#   3. Copy frontend/.env.production.example → frontend/.env.production and fill in values
#
# Usage:
#   chmod +x deploy.sh
#   ./deploy.sh

set -euo pipefail

# ── Config ────────────────────────────────────────────────────────────────────
GITHUB_USER="vishwajeetraut"
RESOURCE_GROUP="greenroom-rg"
API_URL="https://greenroom-api.orangeground-05e56063.swedencentral.azurecontainerapps.io"
PISTON_INTERNAL="http://greenroom-piston.internal.orangeground-05e56063.swedencentral.azurecontainerapps.io/api/v2/execute"
FRONTEND_ORIGIN="https://greenroom-frontend.orangeground-05e56063.swedencentral.azurecontainerapps.io"

# Image tags
SHA=$(git rev-parse --short HEAD 2>/dev/null || echo "latest")
API_IMAGE="ghcr.io/$GITHUB_USER/greenroom-api:$SHA"
PISTON_IMAGE="ghcr.io/$GITHUB_USER/greenroom-piston:$SHA"
FRONTEND_IMAGE="ghcr.io/$GITHUB_USER/greenroom-frontend:$SHA"

# ── Load secrets ──────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ ! -f "$SCRIPT_DIR/backend/.env" ]; then
  echo "ERROR: backend/.env not found"; exit 1
fi
set -a; source "$SCRIPT_DIR/backend/.env"; set +a

if [ ! -f "$SCRIPT_DIR/frontend/.env.production" ]; then
  echo "ERROR: frontend/.env.production not found"
  echo "  Copy frontend/.env.production.example → frontend/.env.production and fill in values"
  exit 1
fi
set -a; source "$SCRIPT_DIR/frontend/.env.production"; set +a

# ── Build & push ──────────────────────────────────────────────────────────────
echo "==> Building backend image..."
docker buildx build \
  --platform linux/amd64 \
  -t "$API_IMAGE" \
  "$SCRIPT_DIR/backend" \
  --push

echo "==> Building piston image (this takes ~3 min first time)..."
docker buildx build \
  --platform linux/amd64 \
  -t "$PISTON_IMAGE" \
  "$SCRIPT_DIR/piston" \
  --push

echo "==> Building frontend image..."
docker buildx build \
  --platform linux/amd64 \
  --build-arg "VITE_SUPABASE_URL=$VITE_SUPABASE_URL" \
  --build-arg "VITE_SUPABASE_ANON_KEY=$VITE_SUPABASE_ANON_KEY" \
  --build-arg "VITE_API_URL=$API_URL/api" \
  -t "$FRONTEND_IMAGE" \
  "$SCRIPT_DIR/frontend" \
  --push

# ── Deploy to Azure Container Apps ───────────────────────────────────────────
echo "==> Deploying backend..."
az containerapp update \
  --name greenroom-api \
  --resource-group "$RESOURCE_GROUP" \
  --image "$API_IMAGE" \
  --set-env-vars \
    GROQ_API_KEY="$GROQ_API_KEY" \
    GROQ_MODEL="${GROQ_MODEL:-llama-3.3-70b-versatile}" \
    SUPABASE_URL="$SUPABASE_URL" \
    SUPABASE_SERVICE_ROLE_KEY="$SUPABASE_SERVICE_ROLE_KEY" \
    FALLBACK_BASE_URL="$FALLBACK_BASE_URL" \
    FALLBACK_API_KEY="$FALLBACK_API_KEY" \
    FALLBACK_MODEL="${FALLBACK_MODEL:-llama3.3:70b}" \
    PISTON_URL="$PISTON_INTERNAL" \
    ALLOWED_ORIGINS="$FRONTEND_ORIGIN" \
  --output none

echo "==> Deploying piston..."
az containerapp update \
  --name greenroom-piston \
  --resource-group "$RESOURCE_GROUP" \
  --image "$PISTON_IMAGE" \
  --output none

echo "==> Deploying frontend..."
az containerapp update \
  --name greenroom-frontend \
  --resource-group "$RESOURCE_GROUP" \
  --image "$FRONTEND_IMAGE" \
  --output none

echo ""
echo "✓ Deployed commit $SHA"
echo ""
echo "  Frontend : $FRONTEND_ORIGIN"
echo "  Backend  : $API_URL"
echo ""
