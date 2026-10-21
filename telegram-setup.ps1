<#
.SYNOPSIS
    CrossFit Registration - Telegram setup for an existing Function App (Windows)

.DESCRIPTION
    Connects a Telegram bot (created with @BotFather) to the Function App: finds
    your chat id, registers the webhook and stores the settings as App Settings.
    The Function App code must already be deployed for the bot to answer.

.EXAMPLE
    .\telegram-setup.ps1 -FunctionAppName "my-crossfit-func" -ResourceGroupName "my-rg"
#>

param(
    [string]$FunctionAppName = "",
    [string]$ResourceGroupName = ""
)

$ErrorActionPreference = "Stop"

function Write-Step { param($Message) Write-Host "`n▶ $Message" -ForegroundColor Cyan }
function Write-Success { param($Message) Write-Host "✓ $Message" -ForegroundColor Green }
function Write-Fail { param($Message) Write-Host "✗ $Message" -ForegroundColor Red; exit 1 }

function Invoke-Telegram {
    param([string]$Method, $Body = @{})
    $json = $Body | ConvertTo-Json -Depth 5
    Invoke-RestMethod -Method Post -Uri "$api/$Method" -ContentType "application/json; charset=utf-8" `
        -Body ([System.Text.Encoding]::UTF8.GetBytes($json))
}

if ([string]::IsNullOrEmpty($FunctionAppName)) { $FunctionAppName = Read-Host "  Function App name" }
if ([string]::IsNullOrEmpty($ResourceGroupName)) { $ResourceGroupName = Read-Host "  Resource Group name" }

#region Bot Token
Write-Step "Connecting the Telegram bot..."

Write-Host "  Create a bot if you don't have one:" -ForegroundColor Yellow
Write-Host "    1. In Telegram, open a chat with @BotFather and send /newbot"
Write-Host "    2. Pick a name and a username (must end with 'bot')"
Write-Host "    3. BotFather replies with a token like 123456789:AA..."
Write-Host ""
$secureToken = Read-Host "    Bot token" -AsSecureString
$telegramBotToken = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
    [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureToken)
)
if ([string]::IsNullOrEmpty($telegramBotToken)) { Write-Fail "Bot token is required" }

$api = "https://api.telegram.org/bot$telegramBotToken"
try { $botUsername = (Invoke-Telegram "getMe").result.username } catch { Write-Fail "Invalid bot token" }
Write-Success "Bot found: @$botUsername"
#endregion

#region Chat ID
Write-Step "Finding your chat id..."

# getUpdates only works while no webhook is set.
Invoke-Telegram "deleteWebhook" | Out-Null
Write-Host "  In Telegram, open https://t.me/$botUsername and press Start (or send /start)" -ForegroundColor Yellow

$telegramChatId = ""
for ($i = 0; $i -lt 60; $i++) {
    $updates = (Invoke-Telegram "getUpdates" @{ timeout = 3 }).result
    $last = $updates | Where-Object { $_.message } | Select-Object -Last 1
    if ($last) { $telegramChatId = [string]$last.message.chat.id; break }
}
if ([string]::IsNullOrEmpty($telegramChatId)) { Write-Fail "No message received from you within 3 minutes - run the script again" }
Write-Success "Chat id: $telegramChatId"
#endregion

#region Webhook
Write-Step "Registering the webhook..."

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

# Telegram sends this secret in a header with every update; the function rejects requests without it.
$bytes = New-Object byte[] 24
[System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
$telegramWebhookSecret = -join ($bytes | ForEach-Object { $_.ToString("x2") })

$result = Invoke-Telegram "setWebhook" @{
    url = "https://$functionHost/api/telegram/webhook?code=$functionKey"
    secret_token = $telegramWebhookSecret
    allowed_updates = @("message", "callback_query")
    drop_pending_updates = $true
}
if (-not $result.ok) { Write-Fail "Failed to register the webhook" }

Invoke-Telegram "setMyCommands" @{ commands = @(
    @{ command = "arbox"; description = "Classes for a day: /arbox wed" },
    @{ command = "sun"; description = "Sunday classes" },
    @{ command = "mon"; description = "Monday classes" },
    @{ command = "tue"; description = "Tuesday classes" },
    @{ command = "wed"; description = "Wednesday classes" },
    @{ command = "thu"; description = "Thursday classes" },
    @{ command = "fri"; description = "Friday classes" },
    @{ command = "sat"; description = "Saturday classes" }
) } | Out-Null
Write-Success "Webhook registered: https://$functionHost/api/telegram/webhook"
#endregion

#region App Settings
Write-Step "Saving Telegram settings..."

az functionapp config appsettings set `
    --name $FunctionAppName `
    --resource-group $ResourceGroupName `
    --settings "TELEGRAM_BOT_TOKEN=$telegramBotToken" "TELEGRAM_CHAT_ID=$telegramChatId" "TELEGRAM_WEBHOOK_SECRET=$telegramWebhookSecret" `
    --output none
Write-Success "Telegram settings saved to $FunctionAppName"

Invoke-Telegram "sendMessage" @{ chat_id = $telegramChatId; text = "Arbox bot connected ✅ Try /wed" } | Out-Null
Write-Success "Test message sent - check Telegram"
#endregion
