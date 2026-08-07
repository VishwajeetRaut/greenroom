# Deployment Guide — Azure (Free Tier)

Stack:
- **Frontend** → Azure Static Web Apps (free)
- **Backend (FastAPI)** → Azure Container Apps, consumption plan (free)
- **Piston (code runner)** → Azure Container Apps, consumption plan (free)
- **Database / Auth** → Supabase (keep as-is)

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
```

Write down the URL printed for `greenroom-api` — you'll need it in Step 4.

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

## Step 3 — Create Azure Static Web Apps

Go to https://portal.azure.com → "Static Web Apps" → Create:
- **Resource group**: greenroom-rg
- **Plan**: Free
- **Region**: East US 2
- **Deployment source**: GitHub → your repo → branch: main
- **Build preset**: Vite
- **App location**: `/frontend`
- **Output location**: `dist`

Azure will commit a workflow file to your repo — **delete that file** (we use our own at `.github/workflows/deploy-frontend.yml`).

After creation, go to the resource → **Manage deployment token** → copy it.
That's your `AZURE_STATIC_WEB_APPS_API_TOKEN` secret.

The Static Web Apps URL will look like:
`https://happy-dune-01234.azurestaticapps.net`

---

## Step 4 — Set GitHub secrets

Go to your repo → Settings → Secrets and variables → Actions → New repository secret:

| Secret | Value |
|--------|-------|
| `AZURE_CLIENT_ID` | from Step 2 JSON |
| `AZURE_TENANT_ID` | from Step 2 JSON |
| `AZURE_SUBSCRIPTION_ID` | from Step 2 JSON |
| `AZURE_RESOURCE_GROUP` | `greenroom-rg` |
| `AZURE_STATIC_WEB_APPS_API_TOKEN` | from Step 3 |
| `VITE_SUPABASE_URL` | Supabase → Project Settings → API → Project URL |
| `VITE_SUPABASE_ANON_KEY` | Supabase → Project Settings → API → anon/public key |
| `VITE_API_URL` | `https://<your-api>.azurecontainerapps.io/api` |
| `SUPABASE_URL` | same as VITE_SUPABASE_URL |
| `SUPABASE_SERVICE_ROLE_KEY` | Supabase → Project Settings → API → service_role key |
| `GROQ_API_KEY` | https://console.groq.com/keys |
| `GROQ_MODEL` | `llama-3.3-70b-versatile` |
| `FALLBACK_BASE_URL` | `https://api.ollama.ai/v1` |
| `FALLBACK_API_KEY` | your Ollama cloud key |
| `FALLBACK_MODEL` | `llama3.3:70b` |
| `ALLOWED_ORIGINS` | `https://<your-app>.azurestaticapps.net` |

---

## Step 4b — Apply database migrations

`supabase/schema.sql` is the initial schema and is still run once, by hand, in
the Supabase SQL editor. Everything in `supabase/migrations/` is applied by the
runner instead:

```bash
cd backend
pip install -r requirements-dev.txt

# Supabase → Project Settings → Database → Connection string.
# This is the Postgres connection string, NOT SUPABASE_URL — the app talks to
# PostgREST, which cannot execute DDL.
export DATABASE_URL='postgresql://postgres:PASSWORD@db.<ref>.supabase.co:5432/postgres'

python scripts/migrate.py --status   # what is applied, what is pending
python scripts/migrate.py            # apply everything pending
```

Each migration runs in a transaction together with its bookkeeping row, so it
is applied exactly once and can never be left half-done. Re-running is a no-op.

**First time only — a database that predates the runner.** The migrations up to
`20260713_analytics_events` were applied by hand, so tell the runner they are
already done rather than letting it apply them a second time:

```bash
python scripts/migrate.py --baseline 20260713_analytics_events
```

This records them without executing them. Run it once per environment, before
the first real `migrate.py` run.

**Migrations go before the code that needs them.** A deploy that ships code
calling a function the database doesn't have yet will fail on every request.

---

## Step 5 — Push and deploy

```bash
git push origin main
```

GitHub Actions runs two workflows:
- **deploy-frontend** — builds Vite app, deploys to Static Web Apps (~2 min)
- **deploy-containers** — builds Docker images, pushes to ghcr.io, updates Container Apps (~5 min)

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
| Azure Static Web Apps | Free | $0 |
| Azure Container Apps | Consumption (free grant: 180K vCPU-s/month) | $0* |
| Supabase | Free | $0 |
| Groq API | Free tier | $0 |
| **Total** | | **$0** |

*Free grant covers ~90 hours of active compute per month. Light traffic stays free.
