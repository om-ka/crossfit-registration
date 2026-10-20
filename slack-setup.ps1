<#
.SYNOPSIS
    CrossFit Registration - Slack setup for an existing Function App (Windows)

.DESCRIPTION
    Generates a Slack app manifest for the Function App, walks you through
    creating the Slack app from it, and stores the Slack credentials as App Settings.

.EXAMPLE
    .\slack-setup.ps1 -FunctionAppName "my-crossfit-func" -ResourceGroupName "my-rg"
#>

param(
    [string]$FunctionAppName = "",
    [string]$ResourceGroupName = ""
)

$ErrorActionPreference = "Stop"

function Write-Step { param($Message) Write-Host "`n▶ $Message" -ForegroundColor Cyan }
function Write-Success { param($Message) Write-Host "✓ $Message" -ForegroundColor Green }
function Write-Warn { param($Message) Write-Host "⚠ $Message" -ForegroundColor Yellow }
function Write-Fail { param($Message) Write-Host "✗ $Message" -ForegroundColor Red; exit 1 }

if ([string]::IsNullOrEmpty($FunctionAppName)) { $FunctionAppName = Read-Host "  Function App name" }
if ([string]::IsNullOrEmpty($ResourceGroupName)) { $ResourceGroupName = Read-Host "  Resource Group name" }

$scriptDir = $PSScriptRoot
if ([string]::IsNullOrEmpty($scriptDir)) { $scriptDir = Get-Location }
$template = Join-Path $scriptDir "slack-manifest.yaml"
$output = Join-Path $scriptDir "slack-manifest.generated.yaml"

#region Generate Manifest
Write-Step "Generating Slack app manifest..."

$functionHost = az functionapp list --resource-group $ResourceGroupName `
    --query "[?name=='$FunctionAppName'].defaultHostName | [0]" -o tsv
if ([string]::IsNullOrEmpty($functionHost)) { Write-Fail "Function App $FunctionAppName not found in $ResourceGroupName" }

# The default key can take a moment to appear right after the first deployment.
$functionKey = ""
for ($i = 0; $i -lt 6; $i++) {
    $functionKey = az functionapp keys list --name $FunctionAppName --resource-group $ResourceGroupName `
        --query functionKeys.default -o tsv 2>$null
    if (-not [string]::IsNullOrEmpty($functionKey)) { break }
    Start-Sleep -Seconds 10
}
if ([string]::IsNullOrEmpty($functionKey)) { Write-Fail "Could not read the Function App's default key" }

$manifest = (Get-Content $template | Where-Object { -not $_.StartsWith("#") }) -join "`n"
$manifest = $manifest.Replace("__FUNCTION_HOST__", $functionHost).Replace("__FUNCTION_KEY__", $functionKey)
Set-Content -Path $output -Value $manifest -Encoding UTF8
Write-Success "Manifest written to $output (contains the function key - don't commit or share it)"

Write-Host "`n  Create the Slack app:" -ForegroundColor Yellow
Write-Host "    1. Go to https://api.slack.com/apps → Create New App → From an app manifest"
Write-Host "    2. Pick your workspace, choose YAML and paste the manifest below, then Create"
Write-Host "    3. Click 'Install to Workspace' and Allow"
Write-Host "    4. In Slack, invite the bot to your channel: /invite @arbox"
Write-Host ""
Write-Host "──────────────────────────── manifest ────────────────────────────"
Write-Host $manifest
Write-Host "──────────────────────────────────────────────────────────────────"
#endregion

#region Slack Credentials
Write-Step "Configuring Slack credentials..."

Write-Host "  Copy these from the Slack app you just created:" -ForegroundColor Yellow
Write-Host "    Bot token:      OAuth & Permissions → Bot User OAuth Token (xoxb-...)"
Write-Host "    Signing secret: Basic Information → App Credentials → Signing Secret"
Write-Host "    Channel ID:     in Slack, open the channel name → About (bottom), e.g. C0123456789"
Write-Host ""
$slackBotToken = Read-Host "    Bot token"
$secureSecret = Read-Host "    Signing secret" -AsSecureString
$slackSigningSecret = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
    [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureSecret)
)
$slackChannelId = Read-Host "    Channel ID"
$slackAllowedUserIds = Read-Host "    Your Slack member ID, to allow only you to press buttons (Enter to skip)"

if ([string]::IsNullOrEmpty($slackBotToken) -or [string]::IsNullOrEmpty($slackSigningSecret) -or [string]::IsNullOrEmpty($slackChannelId)) {
    Write-Fail "Bot token, signing secret and channel ID are all required"
}

$settings = @(
    "SLACK_BOT_TOKEN=$slackBotToken",
    "SLACK_SIGNING_SECRET=$slackSigningSecret",
    "SLACK_CHANNEL_ID=$slackChannelId"
)
if (-not [string]::IsNullOrEmpty($slackAllowedUserIds)) {
    $settings += "SLACK_ALLOWED_USER_IDS=$slackAllowedUserIds"
}

az functionapp config appsettings set `
    --name $FunctionAppName `
    --resource-group $ResourceGroupName `
    --settings @settings `
    --output none
Write-Success "Slack settings saved to $FunctionAppName"
#endregion

#region Test
Write-Step "Sending a test message..."
$body = @{ channel = $slackChannelId; text = "Arbox bot connected ✅ Try /arbox wed" } | ConvertTo-Json
$result = Invoke-RestMethod -Method Post -Uri "https://slack.com/api/chat.postMessage" `
    -Headers @{ Authorization = "Bearer $slackBotToken" } `
    -ContentType "application/json; charset=utf-8" `
    -Body ([System.Text.Encoding]::UTF8.GetBytes($body))
if ($result.ok) {
    Write-Success "Test message posted - check your Slack channel"
} else {
    Write-Warn "Test message failed: $($result.error)"
    Write-Host "    not_in_channel → run /invite @arbox in the channel; invalid_auth → check the bot token"
}
#endregion
