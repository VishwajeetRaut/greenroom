// Azure Container Apps — Greenroom backend
// Deploys the FastAPI backend with session affinity enabled so that the
// in-memory SESSIONS cache stays consistent per replica while the Postgres-
// backed rate limiter is the source of truth for cross-replica rate limiting.
//
// Session affinity (sticky sessions) routes a given browser client to the
// same replica for the lifetime of its session cookie. This eliminates the
// race where Replica A creates a session and Replica B receives the next
// request before _get_session() has a chance to rebuild from Supabase.
//
// Deploy:
//   az deployment group create \
//     --resource-group <rg> \
//     --template-file infra/backend-container-app.bicep \
//     --parameters @infra/backend.parameters.json

@description('Azure Container Apps environment resource ID')
param environmentId string

@description('Container image to deploy (e.g. ghcr.io/owner/greenroom-api:latest)')
param image string

@description('Supabase project URL')
param supabaseUrl string

@secure()
@description('Supabase service-role key')
param supabaseServiceRoleKey string

@secure()
@description('Groq API key')
param groqApiKey string

@description('Allowed CORS origins, comma-separated')
param allowedOrigins string

@description('Minimum replica count (0 = scale to zero)')
param minReplicas int = 0

@description('Maximum replica count')
param maxReplicas int = 2

resource backendApp 'Microsoft.App/containerApps@2023-05-01' = {
  name: 'greenroom-api'
  location: resourceGroup().location
  properties: {
    environmentId: environmentId
    configuration: {
      ingress: {
        external: true
        targetPort: 8000
        // Session affinity — routes the same client to the same replica.
        // Required because SESSIONS is an in-memory cache per process.
        // Safe to enable: the backend is stateless at the data layer (all
        // durable state lives in Supabase) so a replica restart doesn't lose
        // anything — the client just gets routed to a new replica and the
        // session is rebuilt from the DB on the next request.
        stickySessions: {
          affinity: 'sticky'
        }
        transport: 'http'
      }
      secrets: [
        { name: 'supabase-service-role-key', value: supabaseServiceRoleKey }
        { name: 'groq-api-key',              value: groqApiKey }
      ]
    }
    template: {
      containers: [
        {
          name: 'api'
          image: image
          env: [
            { name: 'SUPABASE_URL',              value: supabaseUrl }
            { name: 'SUPABASE_SERVICE_ROLE_KEY', secretRef: 'supabase-service-role-key' }
            { name: 'GROQ_API_KEY',              secretRef: 'groq-api-key' }
            { name: 'ALLOWED_ORIGINS',           value: allowedOrigins }
          ]
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
        }
      ]
      scale: {
        minReplicas: minReplicas
        maxReplicas: maxReplicas
      }
    }
  }
}

output fqdn string = backendApp.properties.configuration.ingress.fqdn
