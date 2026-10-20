#!/bin/bash
#
# CrossFit Registration - Azure Function Deployment Script (macOS/Linux)
#
# Usage:
#   ./deploy.sh
#   ./deploy.sh --function-name "my-crossfit-func" --resource-group "my-rg"
#   ./deploy.sh --skip-prerequisites
#

set -e

# Default values
RESOURCE_GROUP_NAME="rg-crossfit-registration"
LOCATION="israelcentral"
FUNCTION_APP_NAME=""
STORAGE_ACCOUNT_NAME=""
SKIP_PREREQUISITES=false

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
MAGENTA='\033[0;35m'
NC='\033[0m' # No Color

# Helper functions
step() { echo -e "\n${CYAN}▶ $1${NC}"; }
success() { echo -e "${GREEN}✓ $1${NC}"; }
warn() { echo -e "${YELLOW}⚠ $1${NC}"; }
error() { echo -e "${RED}✗ $1${NC}"; exit 1; }

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --resource-group)
            RESOURCE_GROUP_NAME="$2"
            shift 2
            ;;
        --location)
            LOCATION="$2"
            shift 2
            ;;
        --function-name)
            FUNCTION_APP_NAME="$2"
            shift 2
            ;;
        --storage-name)
            STORAGE_ACCOUNT_NAME="$2"
            shift 2
            ;;
        --skip-prerequisites)
            SKIP_PREREQUISITES=true
            shift
            ;;
        -h|--help)
            echo "Usage: $0 [options]"
            echo ""
            echo "Options:"
            echo "  --resource-group NAME    Resource group name (default: rg-crossfit-registration)"
            echo "  --location REGION        Azure region (default: israelcentral)"
            echo "  --function-name NAME     Function app name (must be globally unique)"
            echo "  --storage-name NAME      Storage account name"
            echo "  --skip-prerequisites     Skip prerequisite checks"
            echo "  -h, --help               Show this help message"
            exit 0
            ;;
        *)
            error "Unknown option: $1"
            ;;
    esac
done

# Banner
echo -e "${MAGENTA}"
echo "╔═══════════════════════════════════════════════════════════════╗"
echo "║         CrossFit Registration - Azure Deployment Script       ║"
echo "╚═══════════════════════════════════════════════════════════════╝"
echo -e "${NC}"

#region Prerequisites
if [ "$SKIP_PREREQUISITES" = false ]; then
    step "Checking prerequisites..."

    # Check Azure CLI
    if ! command -v az &> /dev/null; then
        warn "Azure CLI not found. Installing via Homebrew..."

        # Check if Homebrew is installed
        if ! command -v brew &> /dev/null; then
            error "Homebrew is required to install Azure CLI. Install from https://brew.sh"
        fi

        brew update && brew install azure-cli
        success "Azure CLI installed"
    else
        success "Azure CLI is installed ($(az version --query '\"azure-cli\"' -o tsv))"
    fi

    # Check Azure Functions Core Tools
    if ! command -v func &> /dev/null; then
        warn "Azure Functions Core Tools not found. Installing via Homebrew..."
        brew tap azure/functions
        brew install azure-functions-core-tools@4
        success "Azure Functions Core Tools installed"
    else
        success "Azure Functions Core Tools is installed"
    fi

    # Configure Azure CLI to auto-install extensions and allow preview
    echo "  Configuring Azure CLI extensions..."
    az config set extension.use_dynamic_install=yes_without_prompt --only-show-errors
    az config set extension.dynamic_install_allow_preview=true --only-show-errors

    # Pre-install required extensions
    az extension add --name application-insights --only-show-errors 2>/dev/null || true
    success "Azure CLI extensions configured"
fi
#endregion

#region Azure Login
step "Authenticating to Azure..."

# Check if already logged in
if ! az account show &> /dev/null; then
    echo "  Opening browser for Azure login..."
    az login
fi

ACCOUNT_NAME=$(az account show --query "name" -o tsv)
ACCOUNT_USER=$(az account show --query "user.name" -o tsv)
SUBSCRIPTION_ID=$(az account show --query "id" -o tsv)

success "Logged in as: $ACCOUNT_USER"
echo -e "  Subscription: $ACCOUNT_NAME"

# Ask if user wants to change subscription
SUBSCRIPTION_COUNT=$(az account list --query "length(@)")
if [ "$SUBSCRIPTION_COUNT" -gt 1 ]; then
    echo -e "\n${YELLOW}  Available subscriptions:${NC}"
    az account list --query "[].{Name:name, Id:id, IsDefault:isDefault}" -o table

    read -p "  Enter subscription ID to switch (Enter to keep current): " SUB_CHOICE
    if [ -n "$SUB_CHOICE" ]; then
        az account set --subscription "$SUB_CHOICE"
        ACCOUNT_NAME=$(az account show --query "name" -o tsv)
        SUBSCRIPTION_ID=$(az account show --query "id" -o tsv)
        success "Switched to subscription: $ACCOUNT_NAME"
    fi
fi
#endregion

#region Resource Names
step "Configuring resource names..."

# Generate unique suffix
UNIQUE_SUFFIX=$(printf "%04d" $((RANDOM % 10000)))

if [ -z "$FUNCTION_APP_NAME" ]; then
    DEFAULT_FUNC_NAME="func-crossfit-$UNIQUE_SUFFIX"
    read -p "  Function App name (must be globally unique) [$DEFAULT_FUNC_NAME]: " FUNCTION_APP_NAME
    FUNCTION_APP_NAME=${FUNCTION_APP_NAME:-$DEFAULT_FUNC_NAME}
fi

if [ -z "$STORAGE_ACCOUNT_NAME" ]; then
    # Storage account names: 3-24 chars, lowercase letters and numbers only
    DEFAULT_STORAGE_NAME="stcrossfit$UNIQUE_SUFFIX"
    read -p "  Storage Account name [$DEFAULT_STORAGE_NAME]: " STORAGE_ACCOUNT_NAME
    STORAGE_ACCOUNT_NAME=${STORAGE_ACCOUNT_NAME:-$DEFAULT_STORAGE_NAME}
fi

APP_INSIGHTS_NAME="appi-crossfit-$UNIQUE_SUFFIX"

echo -e "\n${YELLOW}  Resources to create:${NC}"
echo "    Resource Group:       $RESOURCE_GROUP_NAME"
echo "    Location:             $LOCATION"
echo "    Storage Account:      $STORAGE_ACCOUNT_NAME"
echo "    Application Insights: $APP_INSIGHTS_NAME"
echo "    Function App:         $FUNCTION_APP_NAME"
#endregion

#region Create Azure Resources
step "Creating Resource Group..."
az group create --name "$RESOURCE_GROUP_NAME" --location "$LOCATION" --output none
success "Resource Group created: $RESOURCE_GROUP_NAME"

step "Creating Storage Account..."
az storage account create \
    --name "$STORAGE_ACCOUNT_NAME" \
    --resource-group "$RESOURCE_GROUP_NAME" \
    --location "$LOCATION" \
    --sku Standard_LRS \
    --kind StorageV2 \
    --output none
success "Storage Account created: $STORAGE_ACCOUNT_NAME"

step "Creating Application Insights..."
az monitor app-insights component create \
    --app "$APP_INSIGHTS_NAME" \
    --resource-group "$RESOURCE_GROUP_NAME" \
    --location "$LOCATION" \
    --kind web \
    --application-type web \
    --output none

APP_INSIGHTS_KEY=$(az monitor app-insights component show \
    --app "$APP_INSIGHTS_NAME" \
    --resource-group "$RESOURCE_GROUP_NAME" \
    --query instrumentationKey -o tsv)

APP_INSIGHTS_CONN_STR=$(az monitor app-insights component show \
    --app "$APP_INSIGHTS_NAME" \
    --resource-group "$RESOURCE_GROUP_NAME" \
    --query connectionString -o tsv)

success "Application Insights created: $APP_INSIGHTS_NAME"

step "Creating Function App (Flex Consumption Plan)..."

# Create Function App with Flex Consumption plan
az functionapp create \
    --name "$FUNCTION_APP_NAME" \
    --resource-group "$RESOURCE_GROUP_NAME" \
    --storage-account "$STORAGE_ACCOUNT_NAME" \
    --flexconsumption-location "$LOCATION" \
    --runtime python \
    --runtime-version 3.11 \
    --functions-version 4 \
    --output none

success "Function App created: $FUNCTION_APP_NAME"

# Configure Application Insights
echo "  Configuring Application Insights connection..."
az functionapp config appsettings set \
    --name "$FUNCTION_APP_NAME" \
    --resource-group "$RESOURCE_GROUP_NAME" \
    --settings "APPINSIGHTS_INSTRUMENTATIONKEY=$APP_INSIGHTS_KEY" "APPLICATIONINSIGHTS_CONNECTION_STRING=$APP_INSIGHTS_CONN_STR" \
    --output none

# Configure CORS for Azure Portal testing
echo "  Configuring CORS for Azure Portal..."
az functionapp cors add \
    --name "$FUNCTION_APP_NAME" \
    --resource-group "$RESOURCE_GROUP_NAME" \
    --allowed-origins "https://portal.azure.com" \
    --output none
success "CORS configured for Azure Portal"
#endregion

#region Environment Variables
step "Configuring environment variables..."

echo -e "\n${YELLOW}  Default configuration:${NC}"
echo "    Class time:         06:00"
echo "    Registration days:  sun,tue,thu"
echo ""

# Prompt for required credentials
echo -e "${YELLOW}  Please enter your Arbox credentials:${NC}"
read -p "    Arbox Email: " ARBOX_EMAIL

# Read password securely (hidden input)
echo -n "    Arbox Password: "
read -s ARBOX_PASSWORD
echo ""


# Optional: customize defaults
echo ""
read -p "  Customize default settings? (y/N): " CUSTOMIZE_DEFAULTS
REGISTRATION_TIME="06:00"
REGISTRATION_DAYS="sun,tue,thu"

if [[ "$CUSTOMIZE_DEFAULTS" =~ ^[Yy]$ ]]; then
    read -p "    Class start time [$REGISTRATION_TIME]: " TIME_INPUT
    REGISTRATION_TIME=${TIME_INPUT:-$REGISTRATION_TIME}

    read -p "    Registration days (comma-separated, e.g., sun,tue,thu) [$REGISTRATION_DAYS]: " DAYS_INPUT
    REGISTRATION_DAYS=${DAYS_INPUT:-$REGISTRATION_DAYS}
fi

# Build settings (Note: TZ is NOT set - Python code defaults to Asia/Jerusalem, and Azure timer uses UTC for cron)
SETTINGS=(
    "ARBOX_USER_EMAIL=$ARBOX_EMAIL"
    "ARBOX_USER_PASSWORD=$ARBOX_PASSWORD"
    "ARBOX_REGISTRATION_START_TIME=$REGISTRATION_TIME"
    "ARBOX_REGISTRATION_DAYS=$REGISTRATION_DAYS"
    "AzureWebJobsFeatureFlags=EnableWorkerIndexing"
)

echo "  Applying settings to Function App..."
az functionapp config appsettings set \
    --name "$FUNCTION_APP_NAME" \
    --resource-group "$RESOURCE_GROUP_NAME" \
    --settings "${SETTINGS[@]}" \
    --output none

success "Environment variables configured"
#endregion

#region Deploy Function Code
step "Deploying function code..."

# Get the script's directory (where the function code is)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "  Source directory: $SCRIPT_DIR"
echo "  Publishing to Azure..."

# Use func azure functionapp publish
pushd "$SCRIPT_DIR" > /dev/null
func azure functionapp publish "$FUNCTION_APP_NAME" --python
popd > /dev/null

success "Function code deployed"
#endregion

#region Slack
"$SCRIPT_DIR/slack-setup.sh" --function-name "$FUNCTION_APP_NAME" --resource-group "$RESOURCE_GROUP_NAME"
#endregion

#region Summary
echo -e "${GREEN}"
echo "╔═══════════════════════════════════════════════════════════════╗"
echo "║                    Deployment Complete!                       ║"
echo "╚═══════════════════════════════════════════════════════════════╝"
echo -e "${NC}"

echo -e "${CYAN}  Resources created:${NC}"
echo "    Resource Group:       $RESOURCE_GROUP_NAME"
echo "    Storage Account:      $STORAGE_ACCOUNT_NAME"
echo "    Application Insights: $APP_INSIGHTS_NAME"
echo "    Function App:         $FUNCTION_APP_NAME"

echo -e "\n${CYAN}  Configuration:${NC}"
echo "    Arbox Email:          $ARBOX_EMAIL"
echo "    Class Time:           $REGISTRATION_TIME"
echo "    Registration Days:    $REGISTRATION_DAYS"

echo -e "\n${CYAN}  Timer Schedule:${NC}"
echo "    Runs every Thursday at 11:58 and 12:58 UTC"
echo "    Waits until 15:00 Israel time to register"

echo -e "\n${CYAN}  Useful commands:${NC}"
echo "    Redo Slack:   ./slack-setup.sh --function-name $FUNCTION_APP_NAME --resource-group $RESOURCE_GROUP_NAME"
echo "    View logs:    az functionapp log tail --name $FUNCTION_APP_NAME --resource-group $RESOURCE_GROUP_NAME"

echo -e "\n${CYAN}  Portal links:${NC}"
echo "    Function App: https://portal.azure.com/#@/resource/subscriptions/$SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP_NAME/providers/Microsoft.Web/sites/$FUNCTION_APP_NAME/overview"
echo ""
#endregion
