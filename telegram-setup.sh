#!/bin/bash
#
# CrossFit Registration - Telegram setup for an existing Function App (macOS/Linux)
#
# Connects a Telegram bot (created with @BotFather) to the Function App: finds
# your chat id, registers the webhook and stores the settings as App Settings.
# The Function App code must already be deployed for the bot to answer.
#
# Usage:
#   ./telegram-setup.sh --function-name "my-crossfit-func" --resource-group "my-rg"
#

set -e

FUNCTION_APP_NAME=""
RESOURCE_GROUP_NAME=""

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Helper functions
step() { echo -e "\n${CYAN}▶ $1${NC}"; }
success() { echo -e "${GREEN}✓ $1${NC}"; }
warn() { echo -e "${YELLOW}⚠ $1${NC}"; }
error() { echo -e "${RED}✗ $1${NC}"; exit 1; }

# Read a field from JSON on stdin, e.g. json_get "result.username"
json_get() {
    python3 -c "import json,sys
try:
    d=json.load(sys.stdin)
    for k in '$1'.split('.'):
        d=d[int(k)] if isinstance(d,list) else d.get(k) if isinstance(d,dict) else None
except Exception:
    d=None
print('' if d is None else d)"
}

while [[ $# -gt 0 ]]; do
    case $1 in
        --function-name)
            FUNCTION_APP_NAME="$2"
            shift 2
            ;;
        --resource-group)
            RESOURCE_GROUP_NAME="$2"
            shift 2
            ;;
        -h|--help)
            echo "Usage: $0 --function-name NAME --resource-group NAME"
            exit 0
            ;;
        *)
            error "Unknown option: $1"
            ;;
    esac
done

[ -z "$FUNCTION_APP_NAME" ] && read -p "  Function App name: " FUNCTION_APP_NAME
[ -z "$RESOURCE_GROUP_NAME" ] && read -p "  Resource Group name: " RESOURCE_GROUP_NAME

command -v python3 &> /dev/null || error "python3 is required"

#region Bot Token
step "Connecting the Telegram bot..."

echo -e "${YELLOW}  Create a bot if you don't have one:${NC}"
echo "    1. In Telegram, open a chat with @BotFather and send /newbot"
echo "    2. Pick a name and a username (must end with 'bot')"
echo "    3. BotFather replies with a token like 123456789:AA..."
echo ""
echo -n "    Bot token: "
read -s TELEGRAM_BOT_TOKEN
echo ""
[ -z "$TELEGRAM_BOT_TOKEN" ] && error "Bot token is required"

API="https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN"
BOT_USERNAME=$(curl -s "$API/getMe" | json_get "result.username")
[ -z "$BOT_USERNAME" ] && error "Invalid bot token"
success "Bot found: @$BOT_USERNAME"
#endregion

#region Chat ID
step "Finding your chat id..."

# getUpdates only works while no webhook is set.
curl -s "$API/deleteWebhook" > /dev/null
echo -e "${YELLOW}  In Telegram, open https://t.me/$BOT_USERNAME and press Start (or send /start)${NC}"

TELEGRAM_CHAT_ID=""
for _ in $(seq 1 60); do
    TELEGRAM_CHAT_ID=$(curl -s "$API/getUpdates?timeout=3" | json_get "result.-1.message.chat.id")
    [ -n "$TELEGRAM_CHAT_ID" ] && break
done
[ -z "$TELEGRAM_CHAT_ID" ] && error "No message received from you within 3 minutes - run the script again"
success "Chat id: $TELEGRAM_CHAT_ID"
#endregion

#region Webhook
step "Registering the webhook..."

FUNCTION_HOST=$(az functionapp list --resource-group "$RESOURCE_GROUP_NAME" \
    --query "[?name=='$FUNCTION_APP_NAME'].defaultHostName | [0]" -o tsv)
[ -z "$FUNCTION_HOST" ] && error "Function App $FUNCTION_APP_NAME not found in $RESOURCE_GROUP_NAME"

# The default key can take a moment to appear right after the first deployment.
FUNCTION_KEY=""
for _ in 1 2 3 4 5 6; do
    FUNCTION_KEY=$(az functionapp keys list --name "$FUNCTION_APP_NAME" --resource-group "$RESOURCE_GROUP_NAME" \
        --query functionKeys.default -o tsv 2>/dev/null || true)
    [ -n "$FUNCTION_KEY" ] && break
    sleep 10
done
[ -z "$FUNCTION_KEY" ] && error "Could not read the Function App's default key"

# Telegram sends this secret in a header with every update; the function rejects requests without it.
TELEGRAM_WEBHOOK_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(24))")

WEBHOOK_OK=$(curl -s "$API/setWebhook" -H "Content-Type: application/json" -d "{
    \"url\": \"https://$FUNCTION_HOST/api/telegram/webhook?code=$FUNCTION_KEY\",
    \"secret_token\": \"$TELEGRAM_WEBHOOK_SECRET\",
    \"allowed_updates\": [\"message\", \"callback_query\"],
    \"drop_pending_updates\": true
}" | json_get "ok")
[ "$WEBHOOK_OK" != "True" ] && error "Failed to register the webhook"

curl -s "$API/setMyCommands" -H "Content-Type: application/json" -d '{"commands": [
    {"command": "arbox", "description": "Classes for a day: /arbox wed"},
    {"command": "sun", "description": "Sunday classes"},
    {"command": "mon", "description": "Monday classes"},
    {"command": "tue", "description": "Tuesday classes"},
    {"command": "wed", "description": "Wednesday classes"},
    {"command": "thu", "description": "Thursday classes"},
    {"command": "fri", "description": "Friday classes"},
    {"command": "sat", "description": "Saturday classes"}
]}' > /dev/null
success "Webhook registered: https://$FUNCTION_HOST/api/telegram/webhook"
#endregion

#region App Settings
step "Saving Telegram settings..."

az functionapp config appsettings set \
    --name "$FUNCTION_APP_NAME" \
    --resource-group "$RESOURCE_GROUP_NAME" \
    --settings "TELEGRAM_BOT_TOKEN=$TELEGRAM_BOT_TOKEN" \
        "TELEGRAM_CHAT_ID=$TELEGRAM_CHAT_ID" \
        "TELEGRAM_WEBHOOK_SECRET=$TELEGRAM_WEBHOOK_SECRET" \
    --output none
success "Telegram settings saved to $FUNCTION_APP_NAME"

curl -s "$API/sendMessage" -H "Content-Type: application/json" \
    -d "{\"chat_id\": \"$TELEGRAM_CHAT_ID\", \"text\": \"Arbox bot connected ✅ Try /wed\"}" > /dev/null
success "Test message sent - check Telegram"
#endregion
