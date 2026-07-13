# Deployment Guide — Azure (Free Tier)

Stack:
- **Frontend** → Azure Container Apps, consumption plan (free) — nginx container serving the built Vite app (see `frontend/Dockerfile`)
- **Backend (FastAPI)** → Azure Container Apps, consumption plan (free)
- **Piston (code runner)** → Azure Container Apps, consumption plan (free)
- **Database / Auth** → Supabase (keep as-is)

All three application containers are built and deployed by
`.github/workflows/deploy-containers.yml`, which runs once `ci.yml` finishes
on `main` and only proceeds if CI passed. It builds all three images, pushes
to GHCR, then runs `az containerapp update` for each so the change actually
goes live without a manual step, and finishes with a smoke test against
`/api/health`.

---

## Prerequisites

```bash
# Install Azure CLI if you don't have it
brew install azure-cli        # macOS
# or: winget install Microsoft.AzureCLI

az login
az account set --subscription "<your subscription id>"
```

---

## Step 1 — Create Azure resources (one-time)

```bash
RG="greenroom-rg"
LOCATION="eastus"
ACA_ENV="greenroom-env"

# Resource group
az group create --name $RG --location $LOCATION

# Container Apps environment (this is the "network" your containers share)
az containerappenv create \
  --name $ACA_ENV \
  --resource-group $RG \
  --location $LOCATION

# Backend API container app (starts with a placeholder image; CI will update it)
az containerapp create \
  --name greenroom-api \
  --resource-group $RG \
  --environment $ACA_ENV \
  --image mcr.microsoft.com/azuredocs/containerapps-helloworld:latest \
  --target-port 8000 \
  --ingress external \
  --min-replicas 0 \
  --max-replicas 2 \
  --cpu 0.5 \
  --memory 1.0Gi

# Piston container app (internal — only the API talks to it)
az containerapp create \
  --name greenroom-piston \
  --resource-group $RG \
  --environment $ACA_ENV \
  --image mcr.microsoft.com/azuredocs/containerapps-helloworld:latest \
  --target-port 2000 \
  --ingress internal \
  --min-replicas 0 \
  --max-replicas 1 \
  --cpu 1.0 \
  --memory 2.0Gi

# Frontend container app (starts with a placeholder image; CI will update it)
az containerapp create \
  --name greenroom-frontend \
  --resource-group $RG \
  --environment $ACA_ENV \
  --image mcr.microsoft.com/azuredocs/containerapps-helloworld:latest \
  --target-port 80 \
  --ingress external \
  --min-replicas 0 \
  --max-replicas 2 \
  --cpu 0.25 \
  --memory 0.5Gi
```

Equivalent Bicep templates for the API and frontend apps live in `infra/` if
you'd rather deploy declaratively (`az deployment group create --template-file
infra/backend-container-app.bicep ...`) — both name their resources
`greenroom-api` / `greenroom-frontend`, matching the `az containerapp create`
commands above and the CI deploy step.

Write down the URLs printed for `greenroom-api` and `greenroom-frontend` — you'll need them in Step 4.

---

## Step 2 — Create a service principal for GitHub Actions

```bash
# Create principal scoped to your resource group
az ad sp create-for-rbac \
  --name "greenroom-github-actions" \
  --role contributor \
  --scopes /subscriptions/<YOUR_SUBSCRIPTION_ID>/resourceGroups/$RG \
  --json-auth
```

This prints a JSON blob. You'll break it into three secrets:
- `clientId` → `AZURE_CLIENT_ID`
- `tenantId` → `AZURE_TENANT_ID`
- `subscriptionId` → `AZURE_SUBSCRIPTION_ID`

Then grant federated identity so OIDC works (no password stored):

```bash
# Replace with your GitHub username and repo name
GITHUB_ORG="VishwajeetRaut"
REPO="greenroom"
APP_ID=$(az ad app list --display-name "greenroom-github-actions" --query '[0].appId' -o tsv)

az ad app federated-credential create \
  --id $APP_ID \
  --parameters '{
    "name": "github-main",
    "issuer": "https://token.actions.githubusercontent.com",
    "subject": "repo:'"$GITHUB_ORG/$REPO"':ref:refs/heads/main",
    "audiences": ["api://AzureADTokenExchange"]
  }'
```

---

## Step 3 — Frontend deployment

No separate setup step needed here — the frontend container app was already
created in Step 1, and `.github/workflows/deploy-containers.yml` builds
`frontend/Dockerfile` (baking in `VITE_SUPABASE_URL` / `VITE_SUPABASE_ANON_KEY`
/ `VITE_API_URL` as Docker build args from the GitHub secrets set in Step 4),
pushes it to GHCR, and runs `az containerapp update` against
`greenroom-frontend` whenever `frontend/**` changes on `main`.

The frontend URL will look like:
`https://greenroom-frontend.<region>.azurecontainerapps.io`

---

## Step 4 — Set GitHub secrets

Go to your repo → Settings → Secrets and variables → Actions → New repository secret:

| Secret | Value |
|--------|-------|
| `AZURE_CLIENT_ID` | from Step 2 JSON |
| `AZURE_TENANT_ID` | from Step 2 JSON |
| `AZURE_SUBSCRIPTION_ID` | from Step 2 JSON |
| `AZURE_RESOURCE_GROUP` | `greenroom-rg` |
| `VITE_SUPABASE_URL` | Supabase → Project Settings → API → Project URL |
| `VITE_SUPABASE_ANON_KEY` | Supabase → Project Settings → API → anon/public key |
| `VITE_API_URL` | `https://<your-api>.azurecontainerapps.io/api` — also used by CI's post-deploy smoke test (`$VITE_API_URL/health`) |
| `SUPABASE_URL` | same as VITE_SUPABASE_URL |
| `SUPABASE_SERVICE_ROLE_KEY` | Supabase → Project Settings → API → service_role key |
| `GROQ_API_KEY` | https://console.groq.com/keys |
| `GROQ_MODEL` | `llama-3.3-70b-versatile` |
| `FALLBACK_BASE_URL` | `https://api.ollama.ai/v1` |
| `FALLBACK_API_KEY` | your Ollama cloud key |
| `FALLBACK_MODEL` | `llama3.3:70b` |
| `ALLOWED_ORIGINS` | `https://<your-frontend>.azurecontainerapps.io` |

Optional repo **variables** (Settings → Secrets and variables → Actions →
Variables tab, not Secrets) if your Container App names differ from the
`greenroom-api` / `greenroom-piston` / `greenroom-frontend` defaults:
`AZURE_API_APP_NAME`, `AZURE_PISTON_APP_NAME`, `AZURE_FRONTEND_APP_NAME`.

---

## Step 5 — Push and deploy

```bash
git push origin main
```

GitHub Actions runs `ci.yml` first (lint, type-check, tests). Once it
succeeds, that triggers `.github/workflows/deploy-containers.yml`, which:
1. Builds and pushes all three images (API, Piston, frontend) to `ghcr.io`
2. Runs `az containerapp update` for each service (~5 min total)
3. Smoke-tests `$VITE_API_URL/health` to confirm the new API revision is actually healthy

If `ci.yml` fails, the deploy workflow never runs.

Watch progress at: https://github.com/VishwajeetRaut/greenroom/actions

---

## ⚠️ Piston privileged mode

Piston's code sandbox (`isolate`) requires `--privileged` Docker mode. Azure Container Apps
**free consumption plan does not support privileged containers**.

**What this means:** The API, auth, chat, and system-design tracks all work. The "Run Code"
and "Run Tests" buttons may return a sandbox error.

**Quick fix if code execution fails** — switch to Judge0 cloud (free tier, no setup):

1. Sign up at https://rapidapi.com/judge0-official/api/judge0-ce and get an API key.
2. Edit `backend/services/piston.py` — replace the `run_code` implementation with Judge0 calls.
   It's a one-file swap; the `RunCodeRequest` model and all callers stay the same.

For a full free deployment with working code execution, use a **Dedicated workload profile**
on Azure Container Apps (D4 plan) which supports privileged mode — but that costs ~$50/month.

---

## Estimated monthly cost

| Service | Plan | Cost |
|---------|------|------|
| Azure Container Apps (API + Piston + frontend) | Consumption (free grant: 180K vCPU-s/month) | $0* |
| Supabase | Free | $0 |
| Groq API | Free tier | $0 |
| **Total** | | **$0** |

*Free grant covers ~90 hours of active compute per month. Light traffic stays free.
