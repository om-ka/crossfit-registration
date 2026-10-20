#!/bin/bash
#
# CrossFit Registration - Slack setup for an existing Function App (macOS/Linux)
#
# Generates a Slack app manifest for the Function App, walks you through
# creating the Slack app from it, and stores the Slack credentials as App Settings.
#
# Usage:
#   ./slack-setup.sh --function-name "my-crossfit-func" --resource-group "my-rg"
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

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEMPLATE="$SCRIPT_DIR/slack-manifest.yaml"
OUTPUT="$SCRIPT_DIR/slack-manifest.generated.yaml"

#region Generate Manifest
step "Generating Slack app manifest..."

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

sed -e "s|__FUNCTION_HOST__|$FUNCTION_HOST|g" -e "s|__FUNCTION_KEY__|$FUNCTION_KEY|g" "$TEMPLATE" \
    | grep -v '^#' > "$OUTPUT"
success "Manifest written to $OUTPUT (contains the function key - don't commit or share it)"

echo -e "\n${YELLOW}  Create the Slack app:${NC}"
echo "    1. Go to https://api.slack.com/apps → Create New App → From an app manifest"
echo "    2. Pick your workspace, choose YAML and paste the manifest below, then Create"
echo "    3. Click 'Install to Workspace' and Allow"
echo "    4. In Slack, invite the bot to your channel: /invite @arbox"
echo ""
echo "──────────────────────────── manifest ────────────────────────────"
cat "$OUTPUT"
echo "──────────────────────────────────────────────────────────────────"
#endregion

#region Slack Credentials
step "Configuring Slack credentials..."

echo -e "${YELLOW}  Copy these from the Slack app you just created:${NC}"
echo "    Bot token:      OAuth & Permissions → Bot User OAuth Token (xoxb-...)"
echo "    Signing secret: Basic Information → App Credentials → Signing Secret"
echo "    Channel ID:     in Slack, open the channel name → About (bottom), e.g. C0123456789"
echo ""
read -p "    Bot token: " SLACK_BOT_TOKEN
echo -n "    Signing secret: "
read -s SLACK_SIGNING_SECRET
echo ""
read -p "    Channel ID: " SLACK_CHANNEL_ID
read -p "    Your Slack member ID, to allow only you to press buttons (Enter to skip): " SLACK_ALLOWED_USER_IDS

[ -z "$SLACK_BOT_TOKEN" ] || [ -z "$SLACK_SIGNING_SECRET" ] || [ -z "$SLACK_CHANNEL_ID" ] \
    && error "Bot token, signing secret and channel ID are all required"

SETTINGS=(
    "SLACK_BOT_TOKEN=$SLACK_BOT_TOKEN"
    "SLACK_SIGNING_SECRET=$SLACK_SIGNING_SECRET"
    "SLACK_CHANNEL_ID=$SLACK_CHANNEL_ID"
)
if [ -n "$SLACK_ALLOWED_USER_IDS" ]; then
    SETTINGS+=("SLACK_ALLOWED_USER_IDS=$SLACK_ALLOWED_USER_IDS")
fi

az functionapp config appsettings set \
    --name "$FUNCTION_APP_NAME" \
    --resource-group "$RESOURCE_GROUP_NAME" \
    --settings "${SETTINGS[@]}" \
    --output none
success "Slack settings saved to $FUNCTION_APP_NAME"
#endregion

#region Test
step "Sending a test message..."
RESULT=$(curl -s -H "Authorization: Bearer $SLACK_BOT_TOKEN" -H "Content-Type: application/json; charset=utf-8" \
    -d "{\"channel\":\"$SLACK_CHANNEL_ID\",\"text\":\"Arbox bot connected ✅ Try /arbox wed\"}" \
    https://slack.com/api/chat.postMessage)
if echo "$RESULT" | grep -q '"ok":true'; then
    success "Test message posted - check your Slack channel"
else
    warn "Test message failed: $RESULT"
    echo "    not_in_channel → run /invite @arbox in the channel; invalid_auth → check the bot token"
fi
#endregion
