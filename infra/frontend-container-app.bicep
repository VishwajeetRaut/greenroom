// Azure Container Apps — Greenroom frontend
// Serves the built Vite app via nginx (see frontend/Dockerfile). All VITE_*
// values are baked into the static bundle at image-build time, not read at
// container runtime, so this template takes no application secrets.
//
// Deploy:
//   az deployment group create \
//     --resource-group <rg> \
//     --template-file infra/frontend-container-app.bicep \
//     --parameters @infra/frontend.parameters.json
//
// After initial creation, .github/workflows/deploy-containers.yml keeps this
// app's image up to date via `az containerapp update` on every push to main.

@description('Azure Container Apps environment resource ID')
param environmentId string

@description('Container image to deploy (e.g. ghcr.io/owner/greenroom-frontend:latest)')
param image string

@description('Minimum replica count (0 = scale to zero)')
param minReplicas int = 0

@description('Maximum replica count')
param maxReplicas int = 2

resource frontendApp 'Microsoft.App/containerApps@2023-05-01' = {
  name: 'greenroom-frontend'
  location: resourceGroup().location
  properties: {
    environmentId: environmentId
    configuration: {
      ingress: {
        external: true
        targetPort: 80
        transport: 'auto'
      }
    }
    template: {
      containers: [
        {
          name: 'frontend'
          image: image
          resources: {
            cpu: json('0.25')
            memory: '0.5Gi'
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

output fqdn string = frontendApp.properties.configuration.ingress.fqdn
