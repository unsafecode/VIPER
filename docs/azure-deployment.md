# Azure deployment

Deploy COBRA/VIPER with Azure Developer CLI (`azd`). Azure deployment is the primary path for hosted testing and tenant rollout.

## Deployment choices

| Mode | What is deployed | Use when |
| --- | --- | --- |
| Backend-only | COBRA FastAPI backend, Container Apps environment, ACR, Storage, Search | The caller will integrate directly with the COBRA API |
| Full stack | Backend-only resources plus the VIPER Next.js UI | Users need the browser UI, auth, and UI state |

Backend-only is the preferred first milestone for a new tenant because it avoids UI-only requirements such as `DATABASE_URL` and `NEXTAUTH_SECRET`.

## Prerequisites

- Azure CLI (`az`)
- Azure Developer CLI (`azd`)
- Docker Desktop
- Azure subscription with permission to create resource groups, Container Apps, ACR, Storage, and Search
- Azure OpenAI or Azure AI Services resource with a chat/vision-capable deployment
- Azure Speech-capable resource when transcript generation is enabled

## Tenant isolation

When working with multiple tenants or subscriptions, isolate Azure CLI and azd state before any Azure command:

```powershell
$env:AZURE_CONFIG_DIR = "C:\Users\<you>\.azure-tenants\<alias>"
$env:AZD_CONFIG_DIR = "C:\Users\<you>\.azd-tenants\<alias>"
az login --tenant "<tenant-id>"
az account set --subscription "<subscription-name-or-id>"
az account show --query "{subscription:name, tenant:tenantId}" -o table
azd auth login
```

## Configure deployment values

Copy the template and set values for your environment:

```powershell
Copy-Item sample.env .env
```

At minimum, configure:

```text
AZURE_OPENAI_GPT_VISION_ENDPOINT="https://<resource>.cognitiveservices.azure.com/"
AZURE_OPENAI_GPT_VISION_API_VERSION="<api-version>"
AZURE_OPENAI_GPT_VISION_DEPLOYMENT="<deployment-name>"
AZURE_OPENAI_GPT_VISION_API_KEY=""

AZURE_SPEECH_REGION="<region>"
AZURE_SPEECH_USE_MANAGED_IDENTITY="true"
AZURE_SPEECH_RESOURCE_ID="/subscriptions/<subscription-id>/resourceGroups/<resource-group>/providers/Microsoft.CognitiveServices/accounts/<resource-name>"
```

Load the `.env` values into the selected azd environment before provisioning:

```powershell
azd env set --file .env
```

See [configuration.md](configuration.md) for the full environment variable reference.

## Backend-only deployment

Use this first when the goal is to expose only the COBRA API.

```powershell
azd env set --file .env
azd env set ENABLE_FRONTEND false
azd provision
azd deploy backend
```

The backend receives Storage/Search settings from infrastructure outputs and can use Entra ID for Azure OpenAI and Speech when API keys are blank.

## Full-stack deployment

Use this when the VIPER UI is required.

```powershell
azd up --no-prompt
```

Full-stack deployment also needs UI runtime values:

```text
DATABASE_URL="<postgresql-connection-string>"
NEXTAUTH_SECRET="<strong-random-secret>"
NEXTAUTH_URL=
```

Leave `NEXTAUTH_URL` blank for Azure deployment so infrastructure uses the frontend Container App URL.

## What the deployment creates

- Azure Resource Group
- Azure Container Registry
- Azure Container Apps managed environment
- COBRA backend Container App
- VIPER frontend Container App when `ENABLE_FRONTEND` is not `false`
- Storage Account
- Azure AI Search
- Private endpoints and private DNS where configured

Cosmos DB is disabled by default because the current backend runtime does not require it.

## Keyless auth and RBAC

The Python backend uses this credential chain:

1. Azure Developer CLI credential
2. Azure CLI credential
3. Managed identity credential

When resource IDs are supplied, `azure.yaml` postprovision hooks assign backend managed identity RBAC:

| Variable | Role assigned |
| --- | --- |
| `AZURE_OPENAI_GPT_VISION_RESOURCE_ID` | `Cognitive Services OpenAI User` |
| `AZURE_SPEECH_RESOURCE_ID` | `Cognitive Services User` |

If you prefer to assign RBAC manually, leave these values blank and assign equivalent roles yourself.

## Post-deploy smoke tests

Check Container App health:

```powershell
$rg = azd env get-value AZURE_RESOURCE_GROUP
$backend = azd env get-value SERVICE_BACKEND_NAME
$frontend = azd env get-value SERVICE_FRONTEND_NAME

az containerapp revision list -g $rg -n $backend --query "[?properties.active].{name:name,health:properties.healthState,traffic:properties.trafficWeight}" -o table
if ($frontend) {
  az containerapp revision list -g $rg -n $frontend --query "[?properties.active].{name:name,health:properties.healthState,traffic:properties.trafficWeight}" -o table
}
```

Unauthenticated frontend smoke expectations:

| Path | Expected |
| --- | --- |
| `/login` | HTTP 200 and rendered sign-in form |
| `/api/auth/session` | HTTP 200 with `{}` |
| `/dashboard` | Redirect to sign-in |

## Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| Deployed env vars contain literal `$(envOrDefault ...)` | azd env values were not loaded or unsupported parameter syntax was used | Use `${VAR}` in `infra\main.parameters.json` and run `azd env set --file .env` |
| `NEXTAUTH_URL` invalid in Azure | Local-only or literal value deployed | Leave `NEXTAUTH_URL` blank and redeploy |
| Backend cannot call Azure OpenAI | Wrong tenant context or missing RBAC | Verify isolated Azure login and assign `Cognitive Services OpenAI User` |
| Speech transcription auth fails | Missing Speech resource ID or RBAC | Set `AZURE_SPEECH_RESOURCE_ID` and assign `Cognitive Services User` |
| First provision cannot pull private ACR image | Managed identity/RBAC is not ready yet | Keep placeholder-image provision and postprovision registry setup in `azure.yaml` |
