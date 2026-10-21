#Requires -Version 5.1
<#
.SYNOPSIS
    Deploys the CrossFit Registration Azure Function with all required resources.

.DESCRIPTION
    This script:
    1. Installs Azure CLI and Az PowerShell module if missing
    2. Authenticates to Azure
    3. Creates Resource Group, Storage Account, Application Insights, and Function App
    4. Deploys the function code
    5. Configures environment variables

.PARAMETER ResourceGroupName
    Name of the Azure Resource Group (default: rg-crossfit-registration)

.PARAMETER Location
    Azure region (default: israelcentral)

.PARAMETER FunctionAppName
    Name of the Function App (must be globally unique)

.EXAMPLE
    .\deploy.ps1 -FunctionAppName "my-crossfit-func"
#>

param(
    [string]$ResourceGroupName = "rg-crossfit-registration",
    [string]$Location = "israelcentral",
    [string]$FunctionAppName = "",
    [string]$StorageAccountName = "",
    [switch]$SkipPrerequisites
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

# Colors for output
function Write-Step { param($Message) Write-Host "`n▶ $Message" -ForegroundColor Cyan }
function Write-Success { param($Message) Write-Host "✓ $Message" -ForegroundColor Green }
function Write-Warning { param($Message) Write-Host "⚠ $Message" -ForegroundColor Yellow }
function Write-Error { param($Message) Write-Host "✗ $Message" -ForegroundColor Red }

# Banner
Write-Host @"

╔═══════════════════════════════════════════════════════════════╗
║         CrossFit Registration - Azure Deployment Script       ║
╚═══════════════════════════════════════════════════════════════╝

"@ -ForegroundColor Magenta

#region Prerequisites
if (-not $SkipPrerequisites) {
    Write-Step "Checking prerequisites..."

    # Check Azure CLI
    $azCliInstalled = $null -ne (Get-Command az -ErrorAction SilentlyContinue)
    if (-not $azCliInstalled) {
        Write-Warning "Azure CLI not found. Installing..."

        # Download and install Azure CLI
        $installerPath = "$env:TEMP\AzureCLI.msi"
        Write-Host "  Downloading Azure CLI installer..."
        Invoke-WebRequest -Uri "https://aka.ms/installazurecliwindows" -OutFile $installerPath

        Write-Host "  Running installer (requires elevation)..."
        Start-Process msiexec.exe -ArgumentList "/I", $installerPath, "/quiet" -Wait
        Remove-Item $installerPath -Force

        # Refresh PATH
        $env:PATH = [System.Environment]::GetEnvironmentVariable("PATH", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("PATH", "User")

        Write-Success "Azure CLI installed"
    } else {
        Write-Success "Azure CLI is installed"
    }

    # Check Azure Functions Core Tools
    $funcToolsInstalled = $null -ne (Get-Command func -ErrorAction SilentlyContinue)
    if (-not $funcToolsInstalled) {
        Write-Warning "Azure Functions Core Tools not found. Installing via npm..."

        # Check if npm is available
        $npmInstalled = $null -ne (Get-Command npm -ErrorAction SilentlyContinue)
        if ($npmInstalled) {
            npm install -g azure-functions-core-tools@4 --unsafe-perm true
            Write-Success "Azure Functions Core Tools installed"
        } else {
            Write-Warning "npm not found. Installing via winget..."
            winget install Microsoft.Azure.FunctionsCoreTools --silent --accept-package-agreements --accept-source-agreements

            # Refresh PATH
            $env:PATH = [System.Environment]::GetEnvironmentVariable("PATH", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("PATH", "User")
        }
    } else {
        Write-Success "Azure Functions Core Tools is installed"
    }

    # Configure Azure CLI to auto-install extensions and allow preview
    Write-Host "  Configuring Azure CLI extensions..."
    az config set extension.use_dynamic_install=yes_without_prompt 2>$null
    az config set extension.dynamic_install_allow_preview=true 2>$null

    # Pre-install required extensions
    az extension add --name application-insights 2>$null
    Write-Success "Azure CLI extensions configured"
}
#endregion

#region Azure Login
Write-Step "Authenticating to Azure..."

# Check if already logged in
$account = az account show 2>$null | ConvertFrom-Json
if (-not $account) {
    Write-Host "  Opening browser for Azure login..."
    az login
    $account = az account show | ConvertFrom-Json
}

Write-Success "Logged in as: $($account.user.name)"
Write-Host "  Subscription: $($account.name)" -ForegroundColor Gray

# Ask if user wants to change subscription
$subscriptions = az account list --query "[].{Name:name, Id:id, IsDefault:isDefault}" | ConvertFrom-Json
if ($subscriptions.Count -gt 1) {
    Write-Host "`n  Available subscriptions:" -ForegroundColor Yellow
    for ($i = 0; $i -lt $subscriptions.Count; $i++) {
        $default = if ($subscriptions[$i].IsDefault) { " (current)" } else { "" }
        Write-Host "    [$i] $($subscriptions[$i].Name)$default"
    }
    $subChoice = Read-Host "  Select subscription number (Enter to keep current)"
    if ($subChoice -ne "" -and $subChoice -match '^\d+$') {
        $selectedSub = $subscriptions[[int]$subChoice]
        az account set --subscription $selectedSub.Id
        Write-Success "Switched to subscription: $($selectedSub.Name)"
    }
}
#endregion

#region Resource Names
Write-Step "Configuring resource names..."

# Generate unique suffix if needed
$uniqueSuffix = (Get-Random -Maximum 9999).ToString("D4")

if ([string]::IsNullOrEmpty($FunctionAppName)) {
    $defaultFuncName = "func-crossfit-$uniqueSuffix"
    $FunctionAppName = Read-Host "  Function App name (must be globally unique) [$defaultFuncName]"
    if ([string]::IsNullOrEmpty($FunctionAppName)) { $FunctionAppName = $defaultFuncName }
}

if ([string]::IsNullOrEmpty($StorageAccountName)) {
    # Storage account names: 3-24 chars, lowercase letters and numbers only
    $defaultStorageName = "stcrossfit$uniqueSuffix"
    $StorageAccountName = Read-Host "  Storage Account name [$defaultStorageName]"
    if ([string]::IsNullOrEmpty($StorageAccountName)) { $StorageAccountName = $defaultStorageName }
}

$AppInsightsName = "appi-crossfit-$uniqueSuffix"

Write-Host "`n  Resources to create:" -ForegroundColor Yellow
Write-Host "    Resource Group:      $ResourceGroupName"
Write-Host "    Location:            $Location"
Write-Host "    Storage Account:     $StorageAccountName"
Write-Host "    Application Insights: $AppInsightsName"
Write-Host "    Function App:        $FunctionAppName"
#endregion

#region Create Azure Resources
Write-Step "Creating Resource Group..."
az group create --name $ResourceGroupName --location $Location --output none
Write-Success "Resource Group created: $ResourceGroupName"

Write-Step "Creating Storage Account..."
az storage account create `
    --name $StorageAccountName `
    --resource-group $ResourceGroupName `
    --location $Location `
    --sku Standard_LRS `
    --kind StorageV2 `
    --output none
Write-Success "Storage Account created: $StorageAccountName"

Write-Step "Creating Application Insights..."
az monitor app-insights component create `
    --app $AppInsightsName `
    --resource-group $ResourceGroupName `
    --location $Location `
    --kind web `
    --application-type web `
    --output none

$appInsightsKey = az monitor app-insights component show `
    --app $AppInsightsName `
    --resource-group $ResourceGroupName `
    --query instrumentationKey -o tsv

$appInsightsConnStr = az monitor app-insights component show `
    --app $AppInsightsName `
    --resource-group $ResourceGroupName `
    --query connectionString -o tsv

Write-Success "Application Insights created: $AppInsightsName"

Write-Step "Creating Function App (Flex Consumption Plan)..."

# Get storage connection string
$storageConnStr = az storage account show-connection-string `
    --name $StorageAccountName `
    --resource-group $ResourceGroupName `
    --query connectionString -o tsv

# Create Function App with Flex Consumption plan
az functionapp create `
    --name $FunctionAppName `
    --resource-group $ResourceGroupName `
    --storage-account $StorageAccountName `
    --flexconsumption-location $Location `
    --runtime python `
    --runtime-version 3.11 `
    --functions-version 4 `
    --output none

Write-Success "Function App created: $FunctionAppName"

# Configure Application Insights
Write-Host "  Configuring Application Insights connection..."
az functionapp config appsettings set `
    --name $FunctionAppName `
    --resource-group $ResourceGroupName `
    --settings "APPINSIGHTS_INSTRUMENTATIONKEY=$appInsightsKey" "APPLICATIONINSIGHTS_CONNECTION_STRING=$appInsightsConnStr" `
    --output none

# Configure CORS for Azure Portal testing
Write-Host "  Configuring CORS for Azure Portal..."
az functionapp cors add `
    --name $FunctionAppName `
    --resource-group $ResourceGroupName `
    --allowed-origins "https://portal.azure.com" `
    --output none
Write-Success "CORS configured for Azure Portal"
#endregion

#region Environment Variables
Write-Step "Configuring environment variables..."

Write-Host "`n  Default configuration:" -ForegroundColor Yellow
Write-Host "    Timezone:           Asia/Jerusalem"
Write-Host "    Class time:         06:00"
Write-Host "    Registration days:  sun,tue,thu"
Write-Host ""

# Prompt for required credentials
Write-Host "  Please enter your Arbox credentials:" -ForegroundColor Yellow
$arboxEmail = Read-Host "    Arbox Email"
$arboxPassword = Read-Host "    Arbox Password" -AsSecureString
$arboxPasswordPlain = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
    [Runtime.InteropServices.Marshal]::SecureStringToBSTR($arboxPassword)
)


# Optional: customize defaults
Write-Host ""
$customizeDefaults = Read-Host "  Customize default settings? (y/N)"
$registrationTime = "06:00"
$registrationDays = "sun,tue,thu"

if ($customizeDefaults -eq "y" -or $customizeDefaults -eq "Y") {
    $timeInput = Read-Host "    Class start time [$registrationTime]"
    if (-not [string]::IsNullOrEmpty($timeInput)) { $registrationTime = $timeInput }

    $daysInput = Read-Host "    Registration days (comma-separated, e.g., sun,tue,thu) [$registrationDays]"
    if (-not [string]::IsNullOrEmpty($daysInput)) { $registrationDays = $daysInput }
}

# Build settings array (Note: TZ is NOT set - Python code defaults to Asia/Jerusalem, and Azure timer uses UTC for cron)
$settings = @(
    "ARBOX_USER_EMAIL=$arboxEmail",
    "ARBOX_USER_PASSWORD=$arboxPasswordPlain",
    "ARBOX_REGISTRATION_START_TIME=$registrationTime",
    "ARBOX_REGISTRATION_DAYS=$registrationDays",
    "AzureWebJobsFeatureFlags=EnableWorkerIndexing"
)

Write-Host "  Applying settings to Function App..."
az functionapp config appsettings set `
    --name $FunctionAppName `
    --resource-group $ResourceGroupName `
    --settings @settings `
    --output none

Write-Success "Environment variables configured"
#endregion

#region Deploy Function Code
Write-Step "Deploying function code..."

# Get the script's directory (where the function code is)
$scriptDir = $PSScriptRoot
if ([string]::IsNullOrEmpty($scriptDir)) {
    $scriptDir = Get-Location
}

Write-Host "  Source directory: $scriptDir"
Write-Host "  Publishing to Azure..."

# Use func azure functionapp publish
Push-Location $scriptDir
try {
    func azure functionapp publish $FunctionAppName --python
} finally {
    Pop-Location
}

Write-Success "Function code deployed"
#endregion

#region Messaging
Write-Step "Messaging (summaries, buttons and day commands)..."
Write-Host "  Set up Slack, Telegram, both or neither (registration works without them)."
$setupTelegram = Read-Host "  Set up Telegram? (y/N)"
if ($setupTelegram -eq "y" -or $setupTelegram -eq "Y") {
    & (Join-Path $scriptDir "telegram-setup.ps1") -FunctionAppName $FunctionAppName -ResourceGroupName $ResourceGroupName
}
$setupSlack = Read-Host "  Set up Slack? (y/N)"
if ($setupSlack -eq "y" -or $setupSlack -eq "Y") {
    & (Join-Path $scriptDir "slack-setup.ps1") -FunctionAppName $FunctionAppName -ResourceGroupName $ResourceGroupName
}
#endregion

#region Summary
Write-Host @"

╔═══════════════════════════════════════════════════════════════╗
║                    Deployment Complete!                       ║
╚═══════════════════════════════════════════════════════════════╝

"@ -ForegroundColor Green

Write-Host "  Resources created:" -ForegroundColor Cyan
Write-Host "    Resource Group:       $ResourceGroupName"
Write-Host "    Storage Account:      $StorageAccountName"
Write-Host "    Application Insights: $AppInsightsName"
Write-Host "    Function App:         $FunctionAppName"

Write-Host "`n  Configuration:" -ForegroundColor Cyan
Write-Host "    Arbox Email:          $arboxEmail"
Write-Host "    Class Time:           $registrationTime"
Write-Host "    Registration Days:    $registrationDays"

Write-Host "`n  Timer Schedule:" -ForegroundColor Cyan
Write-Host "    Runs every Thursday at 11:58 and 12:58 UTC"
Write-Host "    Waits until 15:00 Israel time to register"

Write-Host "`n  Useful commands:" -ForegroundColor Cyan
Write-Host "    Telegram:     .\telegram-setup.ps1 -FunctionAppName $FunctionAppName -ResourceGroupName $ResourceGroupName"
Write-Host "    Redo Slack:   .\slack-setup.ps1 -FunctionAppName $FunctionAppName -ResourceGroupName $ResourceGroupName"
Write-Host "    View logs:    az functionapp log tail --name $FunctionAppName --resource-group $ResourceGroupName"
Write-Host "    View metrics: https://portal.azure.com/#@/resource/subscriptions/$($account.id)/resourceGroups/$ResourceGroupName/providers/Microsoft.Web/sites/$FunctionAppName/appInsightsQueryLogs"

Write-Host "`n  Portal links:" -ForegroundColor Cyan
Write-Host "    Function App: https://portal.azure.com/#@/resource/subscriptions/$($account.id)/resourceGroups/$ResourceGroupName/providers/Microsoft.Web/sites/$FunctionAppName/overview"
Write-Host ""
#endregion
