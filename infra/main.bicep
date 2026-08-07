targetScope = 'resourceGroup'

@description('Azure-Region für alle Platzpflege-Ressourcen.')
param location string = 'germanywestcentral'

@description('Kurzer Namenspräfix für die SSV53-Platzpflege.')
@minLength(3)
@maxLength(20)
param namePrefix string = 'ssv53platzpflege'

@description('Umgebungskürzel.')
@allowed([
  'dev'
  'test'
  'prod'
])
param environmentName string = 'prod'

@description('Azure Functions Timer im sechsstelligen NCRONTAB-Format.')
param timerSchedule string = '0 * * * * *'

@description('Sicherer Betriebsmodus bei der ersten Bereitstellung.')
@allowed([
  'OFF'
  'DRY_RUN'
])
param controlMode string = 'DRY_RUN'

@description('Lesende Live-Abfragen bleiben bei der ersten Bereitstellung deaktiviert.')
param enableLiveReads bool = false

@description('Einziger E-Mail-Empfänger für Platzpflege-Fehler- und Heartbeat-Alarme.')
@minLength(3)
param alertEmail string

@description('Maximal zulässiges Alter der dynamischen Trainings-/Spielplandaten in Minuten.')
@minValue(60)
@maxValue(10080)
param runtimeConfigMaxAgeMinutes int = 1440

@description('Alarmregeln können während des Bootstrap gezielt deaktiviert werden.')
param alertsEnabled bool = true

@description('Dynamische Laufzeitdaten bleiben beim Erstdeployment zunächst deaktiviert.')
param dynamicConfigEnabled bool = false

@description('Maximale Instanzzahl des Flex-Consumption-Plans.')
@minValue(40)
@maxValue(1000)
param maximumInstanceCount int = 40

@description('Arbeitsspeicher pro Flex-Consumption-Instanz.')
@allowed([
  512
  2048
  4096
])
param instanceMemoryMB int = 512

var compactPrefix = toLower(replace(replace(namePrefix, '-', ''), '_', ''))
var resourceToken = take(toLower(uniqueString(subscription().id, resourceGroup().id, environmentName, location)), 8)
var storageAccountName = take('${compactPrefix}${environmentName}${resourceToken}', 24)
var functionAppName = take('func-${namePrefix}-${environmentName}-${resourceToken}', 60)
var functionPlanName = take('plan-${namePrefix}-${environmentName}-${resourceToken}', 60)
var managedIdentityName = take('id-${namePrefix}-${environmentName}-${resourceToken}', 128)
var logAnalyticsName = take('log-${namePrefix}-${environmentName}-${resourceToken}', 63)
var applicationInsightsName = take('appi-${namePrefix}-${environmentName}-${resourceToken}', 260)
var keyVaultName = take('kv-${namePrefix}-${environmentName}-${resourceToken}', 24)
var deploymentStorageContainerName = 'app-package-${take(resourceToken, 8)}'
var runtimeConfigContainerName = 'runtime-config'
var stateTableName = 'MowerAutomationState'
var actionGroupName = take('ag-${namePrefix}-${environmentName}-${resourceToken}', 64)
var heartbeatAlertName = take('alert-${namePrefix}-${environmentName}-heartbeat-${resourceToken}', 260)
var failureAlertName = take('alert-${namePrefix}-${environmentName}-failure-${resourceToken}', 260)

var storageBlobDataOwnerRoleId = 'b7e6dc6d-f1e8-4753-8033-0f276bb0955b'
var storageQueueDataContributorRoleId = '974c5e8b-45b9-4653-ba55-5f855dd0fb88'
var storageTableDataContributorRoleId = '0a9a7e1f-b9d0-4cc4-a60d-0319b160aaa3'
var monitoringMetricsPublisherRoleId = '3913510d-42f4-4e42-8a64-420c390055eb'
var keyVaultSecretsUserRoleId = '4633458b-17de-408a-b874-0445c86b69e6'

var tags = {
  application: 'SSV53 Platzpflege'
  environment: environmentName
  managedBy: 'Bicep'
  safetyStage: 'DRY_RUN'
}

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: logAnalyticsName
  location: location
  tags: tags
  properties: any({
    retentionInDays: 30
    features: {
      searchVersion: 1
    }
    sku: {
      name: 'PerGB2018'
    }
  })
}

resource applicationInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: applicationInsightsName
  location: location
  kind: 'web'
  tags: tags
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logAnalytics.id
    DisableLocalAuth: true
  }
}

resource actionGroup 'Microsoft.Insights/actionGroups@2023-01-01' = {
  name: actionGroupName
  location: 'global'
  tags: tags
  properties: {
    enabled: true
    groupShortName: 'SSV53Pflege'
    emailReceivers: [
      {
        name: 'Thomas'
        emailAddress: alertEmail
        useCommonAlertSchema: true
      }
    ]
    smsReceivers: []
    webhookReceivers: []
    itsmReceivers: []
    azureAppPushReceivers: []
    automationRunbookReceivers: []
    voiceReceivers: []
    logicAppReceivers: []
    azureFunctionReceivers: []
    eventHubReceivers: []
    armRoleReceivers: []
  }
}

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageAccountName
  location: location
  kind: 'StorageV2'
  sku: {
    name: 'Standard_LRS'
  }
  tags: tags
  properties: {
    accessTier: 'Hot'
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    dnsEndpointType: 'Standard'
    minimumTlsVersion: 'TLS1_2'
    publicNetworkAccess: 'Enabled'
    networkAcls: {
      bypass: 'AzureServices'
      defaultAction: 'Allow'
    }
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storage
  name: 'default'
  properties: {
    deleteRetentionPolicy: {
      enabled: true
      days: 7
    }
  }
}

resource deploymentContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: deploymentStorageContainerName
  properties: {
    publicAccess: 'None'
  }
}

resource runtimeConfigContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: runtimeConfigContainerName
  properties: {
    publicAccess: 'None'
  }
}

resource tableService 'Microsoft.Storage/storageAccounts/tableServices@2023-05-01' = {
  parent: storage
  name: 'default'
  properties: {}
}

resource stateTable 'Microsoft.Storage/storageAccounts/tableServices/tables@2023-05-01' = {
  parent: tableService
  name: stateTableName
  properties: {
    signedIdentifiers: []
  }
}

resource managedIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: managedIdentityName
  location: location
  tags: tags
}

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: keyVaultName
  location: location
  tags: tags
  properties: {
    tenantId: tenant().tenantId
    sku: {
      family: 'A'
      name: 'standard'
    }
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 90
    enablePurgeProtection: true
    publicNetworkAccess: 'Enabled'
  }
}

resource roleAssignmentBlobOwner 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(subscription().id, storage.id, managedIdentity.id, 'Storage Blob Data Owner')
  scope: storage
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageBlobDataOwnerRoleId)
    principalId: managedIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource roleAssignmentQueueContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(subscription().id, storage.id, managedIdentity.id, 'Storage Queue Data Contributor')
  scope: storage
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageQueueDataContributorRoleId)
    principalId: managedIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource roleAssignmentTableContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(subscription().id, storage.id, managedIdentity.id, 'Storage Table Data Contributor')
  scope: storage
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageTableDataContributorRoleId)
    principalId: managedIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource roleAssignmentMonitoringPublisher 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(subscription().id, applicationInsights.id, managedIdentity.id, 'Monitoring Metrics Publisher')
  scope: applicationInsights
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', monitoringMetricsPublisherRoleId)
    principalId: managedIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource roleAssignmentKeyVaultSecretsUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(subscription().id, keyVault.id, managedIdentity.id, 'Key Vault Secrets User')
  scope: keyVault
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', keyVaultSecretsUserRoleId)
    principalId: managedIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource functionPlan 'Microsoft.Web/serverfarms@2024-04-01' = {
  name: functionPlanName
  location: location
  kind: 'functionapp'
  tags: tags
  sku: {
    name: 'FC1'
    tier: 'FlexConsumption'
  }
  properties: {
    reserved: true
  }
}

resource functionApp 'Microsoft.Web/sites@2024-04-01' = {
  name: functionAppName
  location: location
  kind: 'functionapp,linux'
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${managedIdentity.id}': {}
    }
  }
  properties: {
    serverFarmId: functionPlan.id
    httpsOnly: true
    publicNetworkAccess: 'Enabled'
    keyVaultReferenceIdentity: managedIdentity.id
    siteConfig: {
      minTlsVersion: '1.2'
      ftpsState: 'Disabled'
    }
    functionAppConfig: {
      deployment: {
        storage: {
          type: 'blobContainer'
          value: '${storage.properties.primaryEndpoints.blob}${deploymentStorageContainerName}'
          authentication: {
            type: 'UserAssignedIdentity'
            userAssignedIdentityResourceId: managedIdentity.id
          }
        }
      }
      scaleAndConcurrency: {
        maximumInstanceCount: maximumInstanceCount
        instanceMemoryMB: instanceMemoryMB
      }
      runtime: {
        name: 'python'
        version: '3.12'
      }
    }
  }
  dependsOn: [
    roleAssignmentBlobOwner
    roleAssignmentQueueContributor
    roleAssignmentTableContributor
    roleAssignmentMonitoringPublisher
    roleAssignmentKeyVaultSecretsUser
  ]
}

resource functionAppSettings 'Microsoft.Web/sites/config@2024-04-01' = {
  parent: functionApp
  name: 'appsettings'
  properties: {
    AzureWebJobsStorage__accountName: storage.name
    AzureWebJobsStorage__credential: 'managedidentity'
    AzureWebJobsStorage__clientId: managedIdentity.properties.clientId
    APPLICATIONINSIGHTS_CONNECTION_STRING: applicationInsights.properties.ConnectionString
    APPLICATIONINSIGHTS_AUTHENTICATION_STRING: 'ClientId=${managedIdentity.properties.clientId};Authorization=AAD'
    TIMER_SCHEDULE: timerSchedule
    CONTROL_MODE: controlMode
    ENABLE_LIVE_READS: string(enableLiveReads)
    SSV53_TIMEZONE: 'Europe/Berlin'
    SSV53_STATE_TABLE_NAME: stateTableName
    SSV53_STORAGE_ACCOUNT_URL: storage.properties.primaryEndpoints.table
    SSV53_DYNAMIC_CONFIG_ENABLED: string(dynamicConfigEnabled)
    SSV53_CONFIG_STORAGE_ACCOUNT_URL: storage.properties.primaryEndpoints.blob
    SSV53_CONFIG_CONTAINER: runtimeConfigContainerName
    SSV53_CONFIG_MANAGED_IDENTITY_CLIENT_ID: managedIdentity.properties.clientId
    SSV53_CONFIG_MAX_AGE_MINUTES: string(runtimeConfigMaxAgeMinutes)
    SSV53_CONFIG_CACHE_DIR: '/tmp/ssv53-config'
    HUSQVARNA_CLIENT_ID: '@Microsoft.KeyVault(SecretUri=${keyVault.properties.vaultUri}secrets/husqvarna-client-id)'
    HUSQVARNA_CLIENT_SECRET: '@Microsoft.KeyVault(SecretUri=${keyVault.properties.vaultUri}secrets/husqvarna-client-secret)'
    HYDRAWISE_API_KEY: '@Microsoft.KeyVault(SecretUri=${keyVault.properties.vaultUri}secrets/hydrawise-api-key)'
    HYDRAWISE_CONTROLLER_ID: '@Microsoft.KeyVault(SecretUri=${keyVault.properties.vaultUri}secrets/hydrawise-controller-id)'
  }
}


resource heartbeatAlert 'Microsoft.Insights/scheduledQueryRules@2023-12-01' = {
  name: heartbeatAlertName
  location: location
  kind: 'LogAlert'
  tags: tags
  properties: {
    displayName: 'SSV53 Platzpflege – Heartbeat fehlt'
    description: 'Alarm, wenn innerhalb von 15 Minuten kein SSV53_CONTROL_CYCLE protokolliert wurde.'
    enabled: alertsEnabled
    severity: 2
    evaluationFrequency: 'PT5M'
    windowSize: 'PT15M'
    autoMitigate: true
    checkWorkspaceAlertsStorageConfigured: false
    skipQueryValidation: true
    scopes: [
      applicationInsights.id
    ]
    criteria: {
      allOf: [
        {
          query: 'traces | where message startswith "SSV53_CONTROL_CYCLE"'
          timeAggregation: 'Count'
          operator: 'LessThan'
          threshold: 1
          failingPeriods: {
            minFailingPeriodsToAlert: 1
            numberOfEvaluationPeriods: 1
          }
        }
      ]
    }
    actions: {
      actionGroups: [
        actionGroup.id
      ]
    }
  }
}

resource failureAlert 'Microsoft.Insights/scheduledQueryRules@2023-12-01' = {
  name: failureAlertName
  location: location
  kind: 'LogAlert'
  tags: tags
  properties: {
    displayName: 'SSV53 Platzpflege – Fehler'
    description: 'Alarm bei mindestens einer Application-Insights-Exception innerhalb von 5 Minuten.'
    enabled: alertsEnabled
    severity: 2
    evaluationFrequency: 'PT5M'
    windowSize: 'PT5M'
    autoMitigate: true
    checkWorkspaceAlertsStorageConfigured: false
    skipQueryValidation: true
    scopes: [
      applicationInsights.id
    ]
    criteria: {
      allOf: [
        {
          query: 'exceptions'
          timeAggregation: 'Count'
          operator: 'GreaterThan'
          threshold: 0
          failingPeriods: {
            minFailingPeriodsToAlert: 1
            numberOfEvaluationPeriods: 1
          }
        }
      ]
    }
    actions: {
      actionGroups: [
        actionGroup.id
      ]
    }
  }
}

output deployedFunctionAppName string = functionApp.name
output functionAppResourceId string = functionApp.id
output managedIdentityClientId string = managedIdentity.properties.clientId
output deployedKeyVaultName string = keyVault.name
output deployedStorageAccountName string = storage.name
output deployedStateTableName string = stateTableName
output deploymentContainerName string = deploymentContainer.name
output runtimeConfigContainerName string = runtimeConfigContainer.name
output alertActionGroupName string = actionGroup.name
output deployedApplicationInsightsName string = applicationInsights.name
