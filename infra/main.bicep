targetScope = 'resourceGroup'

@description('Short name prefix. Must match bootstrap.bicep so existing lookups resolve.')
@minLength(3)
@maxLength(12)
param prefix string = 'ccite'

@description('Azure region for every resource in this group.')
param location string = resourceGroup().location

@description('API image. The deploy workflow passes <acr>/capitalcite:<github-sha>. The default is not capitalcite.')
param apiImage string = 'mcr.microsoft.com/k8se/quickstart:latest'

@description('Qdrant image. Pin the same major as docker-compose.yml.')
param qdrantImage string = 'docker.io/qdrant/qdrant:v1.16.2'

@minValue(0)
@maxValue(5)
param apiMinReplicas int = 0

@minValue(1)
@maxValue(5)
param apiMaxReplicas int = 1

@minValue(0)
@maxValue(3)
param qdrantMinReplicas int = 1

@description('Declared SEC User-Agent. Must contain @. Not a secret.')
param secUserAgent string = 'capitalcite-azure noreply@example.com'

@description('ASK_MODEL for the API process. fake boots without OPENAI_API_KEY.')
@allowed(['fake', 'openai'])
param askModel string = 'fake'

@description('Optional Key Vault secret URI for OPENAI_API_KEY. Empty skips the env var.')
param openaiSecretUri string = ''

@description('Optional Key Vault secret URI for LANGFUSE_PUBLIC_KEY.')
param langfusePublicSecretUri string = ''

@description('Optional Key Vault secret URI for LANGFUSE_SECRET_KEY.')
param langfuseSecretSecretUri string = ''

@description('Langfuse host when keys are set. Empty leaves the SDK default.')
param langfuseHost string = ''

var unique = uniqueString(resourceGroup().id)
var acrName = '${prefix}${take(unique, 8)}'
var kvName = take('${prefix}kv${take(unique, 8)}', 24)
var logsName = take('${prefix}-logs-${unique}', 63)
var envName = take('${prefix}-env-${take(unique, 8)}', 32)
var storageName = take('st${prefix}${take(unique, 8)}', 24)
var shareName = 'qdrant'
var apiAppName = take('${prefix}-api', 32)
var qdrantAppName = take('${prefix}-qdrant', 32)
var runtimeIdentityName = take('${prefix}-rt', 24)
var useOpenai = !empty(openaiSecretUri)
var useLangfusePublic = !empty(langfusePublicSecretUri)
var useLangfuseSecret = !empty(langfuseSecretSecretUri)
var useLangfuseHost = !empty(langfuseHost)

resource runtimeIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' existing = {
  name: runtimeIdentityName
}

resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = {
  name: acrName
}

resource kv 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: kvName
}

resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: logsName
  location: location
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
    publicNetworkAccessForIngestion: 'Enabled'
    publicNetworkAccessForQuery: 'Enabled'
    features: {
      enableLogAccessUsingOnlyResourcePermissions: true
    }
  }
}

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageName
  location: location
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    allowBlobPublicAccess: false
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    publicNetworkAccess: 'Enabled'
  }
}

resource fileService 'Microsoft.Storage/storageAccounts/fileServices@2023-05-01' = {
  parent: storage
  name: 'default'
}

resource qdrantShare 'Microsoft.Storage/storageAccounts/fileServices/shares@2023-05-01' = {
  parent: fileService
  name: shareName
  properties: {
    shareQuota: 10
  }
}

resource env 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: envName
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logs.properties.customerId
        sharedKey: logs.listKeys().primarySharedKey
      }
    }
    zoneRedundant: false
    workloadProfiles: [
      {
        name: 'Consumption'
        workloadProfileType: 'Consumption'
      }
    ]
  }
}

resource envStorage 'Microsoft.App/managedEnvironments/storages@2024-03-01' = {
  parent: env
  name: shareName
  properties: {
    azureFile: {
      accountName: storage.name
      accountKey: storage.listKeys().keys[0].value
      shareName: shareName
      accessMode: 'ReadWrite'
    }
  }
}

resource qdrantApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: qdrantAppName
  location: location
  properties: {
    environmentId: env.id
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: false
        targetPort: 6333
        transport: 'http'
        allowInsecure: true
        traffic: [
          {
            latestRevision: true
            weight: 100
          }
        ]
      }
    }
    template: {
      containers: [
        {
          name: 'qdrant'
          image: qdrantImage
          resources: {
            cpu: json('0.25')
            memory: '0.5Gi'
          }
          env: [
            {
              name: 'QDRANT__SERVICE__HTTP_PORT'
              value: '6333'
            }
          ]
          volumeMounts: [
            {
              volumeName: 'qdrant-storage'
              mountPath: '/qdrant/storage'
            }
          ]
          probes: [
            {
              type: 'Liveness'
              httpGet: {
                path: '/healthz'
                port: 6333
                scheme: 'HTTP'
              }
              initialDelaySeconds: 10
              periodSeconds: 20
            }
          ]
        }
      ]
      scale: {
        minReplicas: qdrantMinReplicas
        maxReplicas: max(qdrantMinReplicas, 1)
      }
      volumes: [
        {
          name: 'qdrant-storage'
          storageType: 'AzureFile'
          storageName: shareName
        }
      ]
    }
  }
  dependsOn: [
    envStorage
    qdrantShare
  ]
}

var apiSecrets = concat(
  useOpenai
    ? [
        {
          name: 'openai-api-key'
          keyVaultUrl: openaiSecretUri
          identity: runtimeIdentity.id
        }
      ]
    : [],
  useLangfusePublic
    ? [
        {
          name: 'langfuse-public-key'
          keyVaultUrl: langfusePublicSecretUri
          identity: runtimeIdentity.id
        }
      ]
    : [],
  useLangfuseSecret
    ? [
        {
          name: 'langfuse-secret-key'
          keyVaultUrl: langfuseSecretSecretUri
          identity: runtimeIdentity.id
        }
      ]
    : []
)

var openaiEnv = useOpenai
  ? [
      {
        name: 'OPENAI_API_KEY'
        secretRef: 'openai-api-key'
      }
    ]
  : []

var langfusePublicEnv = useLangfusePublic
  ? [
      {
        name: 'LANGFUSE_PUBLIC_KEY'
        secretRef: 'langfuse-public-key'
      }
    ]
  : []

var langfuseSecretEnv = useLangfuseSecret
  ? [
      {
        name: 'LANGFUSE_SECRET_KEY'
        secretRef: 'langfuse-secret-key'
      }
    ]
  : []

var langfuseHostEnv = useLangfuseHost
  ? [
      {
        name: 'LANGFUSE_HOST'
        value: langfuseHost
      }
    ]
  : []

var tracingEnabled = (useLangfusePublic && useLangfuseSecret) ? 'true' : 'false'

var apiEnv = concat(
  [
    {
      name: 'SEC_USER_AGENT'
      value: secUserAgent
    }
    {
      name: 'ASK_MODEL'
      value: askModel
    }
    {
      name: 'APP_ENV'
      value: 'prod'
    }
    {
      name: 'QDRANT_URL'
      value: 'http://${qdrantAppName}:6333'
    }
    {
      name: 'AUDIT_LOG_PATH'
      value: '/home/app/data/audit.jsonl'
    }
    {
      name: 'DATA_DIR'
      value: '/home/app/data'
    }
    {
      name: 'LANGFUSE_TRACING_ENABLED'
      value: tracingEnabled
    }
  ],
  openaiEnv,
  langfusePublicEnv,
  langfuseSecretEnv,
  langfuseHostEnv
)

resource apiApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: apiAppName
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${runtimeIdentity.id}': {}
    }
  }
  properties: {
    environmentId: env.id
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8000
        transport: 'http'
        allowInsecure: false
        traffic: [
          {
            latestRevision: true
            weight: 100
          }
        ]
      }
      registries: [
        {
          server: acr.properties.loginServer
          identity: runtimeIdentity.id
        }
      ]
      secrets: apiSecrets
    }
    template: {
      containers: [
        {
          name: 'api'
          image: apiImage
          resources: {
            cpu: json('0.25')
            memory: '0.5Gi'
          }
          env: apiEnv
          probes: [
            {
              type: 'Liveness'
              httpGet: {
                path: '/health'
                port: 8000
                scheme: 'HTTP'
              }
              initialDelaySeconds: 5
              periodSeconds: 15
            }
          ]
        }
      ]
      scale: {
        minReplicas: apiMinReplicas
        maxReplicas: apiMaxReplicas
        rules: [
          {
            name: 'http'
            http: {
              metadata: {
                concurrentRequests: '50'
              }
            }
          }
        ]
      }
    }
  }
}

output apiFqdn string = apiApp.properties.configuration.ingress.fqdn
output apiAppName string = apiApp.name
output qdrantFqdn string = qdrantApp.properties.configuration.ingress.fqdn
output qdrantExternal bool = qdrantApp.properties.configuration.ingress.external
output acrLoginServer string = acr.properties.loginServer
output acrName string = acr.name
output keyVaultName string = kv.name
output runtimeIdentityClientId string = runtimeIdentity.properties.clientId
output resourceGroupName string = resourceGroup().name
