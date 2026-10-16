targetScope = 'resourceGroup'

@description('Short name prefix. ACR keeps letters and digits only. Must match main.bicep.')
@minLength(3)
@maxLength(12)
param prefix string = 'ccite'

@description('Azure region for every resource in this group.')
param location string = resourceGroup().location

@description('GitHub org used in the OIDC federated credential subject.')
param githubOrg string = 'naveed949'

@description('GitHub repo used in the OIDC federated credential subject.')
param githubRepo string = 'capitalcite'

@description('GitHub Environment name for OIDC. Create this environment before the first workflow deploy.')
param githubEnvironment string = 'azure'

var unique = uniqueString(resourceGroup().id)
var acrName = '${prefix}${take(unique, 8)}'
var kvName = take('${prefix}kv${take(unique, 8)}', 24)
var runtimeIdentityName = take('${prefix}-rt', 24)
var deployIdentityName = take('${prefix}-deploy', 24)

var acrPullRoleId = '7f951dda-4ed3-4680-a7ca-43fe172d538d'
var acrPushRoleId = '8311e382-0749-4cb8-b69d-38feb7eb23f0'
var kvSecretsUserRoleId = '4633458b-17de-408a-b874-0445c86b69e6'
var contributorRoleId = 'b24988ac-6180-42a0-ab88-20f7382dd24c'

resource runtimeIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: runtimeIdentityName
  location: location
}

resource deployIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: deployIdentityName
  location: location
}

resource githubFederationEnv 'Microsoft.ManagedIdentity/userAssignedIdentities/federatedIdentityCredentials@2023-01-31' = {
  parent: deployIdentity
  name: 'github-${githubEnvironment}'
  properties: {
    issuer: 'https://token.actions.githubusercontent.com'
    subject: 'repo:${githubOrg}/${githubRepo}:environment:${githubEnvironment}'
    audiences: [
      'api://AzureADTokenExchange'
    ]
  }
}

resource githubFederationMain 'Microsoft.ManagedIdentity/userAssignedIdentities/federatedIdentityCredentials@2023-01-31' = {
  parent: deployIdentity
  name: 'github-main'
  properties: {
    issuer: 'https://token.actions.githubusercontent.com'
    subject: 'repo:${githubOrg}/${githubRepo}:ref:refs/heads/main'
    audiences: [
      'api://AzureADTokenExchange'
    ]
  }
}

resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: acrName
  location: location
  sku: {
    name: 'Basic'
  }
  properties: {
    adminUserEnabled: false
    publicNetworkAccess: 'Enabled'
    zoneRedundancy: 'Disabled'
  }
}

resource kv 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: kvName
  location: location
  properties: {
    sku: {
      family: 'A'
      name: 'standard'
    }
    tenantId: subscription().tenantId
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
    publicNetworkAccess: 'Enabled'
    enabledForDeployment: false
    enabledForTemplateDeployment: false
    enabledForDiskEncryption: false
  }
}

resource acrPullRuntime 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(acr.id, runtimeIdentity.id, acrPullRoleId)
  scope: acr
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPullRoleId)
    principalId: runtimeIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource acrPushDeploy 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(acr.id, deployIdentity.id, acrPushRoleId)
  scope: acr
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPushRoleId)
    principalId: deployIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource kvSecretsRuntime 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(kv.id, runtimeIdentity.id, kvSecretsUserRoleId)
  scope: kv
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', kvSecretsUserRoleId)
    principalId: runtimeIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource rgContributorDeploy 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(resourceGroup().id, deployIdentity.id, contributorRoleId)
  scope: resourceGroup()
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', contributorRoleId)
    principalId: deployIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

output acrLoginServer string = acr.properties.loginServer
output acrName string = acr.name
output keyVaultName string = kv.name
output runtimeIdentityClientId string = runtimeIdentity.properties.clientId
output deployIdentityClientId string = deployIdentity.properties.clientId
output resourceGroupName string = resourceGroup().name
