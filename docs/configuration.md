# Configuration reference

Configuration is supplied through `.env` locally and through azd environment values for deployment. Start from `sample.env`.

```powershell
Copy-Item sample.env .env
azd env set --file .env
```

To quickly fill local `.env` from the selected azd environment after provisioning or deployment:

```powershell
azd env get-values |
  Where-Object { $_ -match '^[A-Za-z_][A-Za-z0-9_]*=' } |
  Set-Content .env
```

This overwrites `.env`, which is ignored by git.

## COBRA backend

| Variable | Required | Description |
| --- | --- | --- |
| `AZURE_OPENAI_GPT_VISION_ENDPOINT` | Yes for analysis | Azure OpenAI or Azure AI Services endpoint |
| `AZURE_OPENAI_GPT_VISION_API_VERSION` | Yes for analysis | API version used by the OpenAI SDK |
| `AZURE_OPENAI_GPT_VISION_DEPLOYMENT` | Yes for analysis | Deployment name used for chat/vision analysis |
| `AZURE_OPENAI_GPT_VISION_API_KEY` | No | Optional API key; leave blank for Entra ID |
| `AZURE_OPENAI_GPT_VISION_RESOURCE_ID` | No | Optional resource ID used by deployment hooks to assign backend RBAC |
| `AZURE_SPEECH_REGION` | Yes for transcripts | Speech region |
| `AZURE_SPEECH_ENDPOINT` | No | Optional Speech endpoint override |
| `AZURE_SPEECH_USE_MANAGED_IDENTITY` | Yes for keyless Speech | Use `true` for Entra ID auth |
| `AZURE_SPEECH_RESOURCE_ID` | Yes for keyless Speech | Required by Speech SDK for `aad#resourceId#token` auth |
| `AZURE_SPEECH_API_KEY` | No | Optional Speech key when managed identity is disabled |
| `AZURE_STORAGE_ACCOUNT_URL` | Azure upload/deployment | Blob account URL; infra can generate it when blank |
| `AZURE_STORAGE_VIDEO_CONTAINER` | Azure upload/deployment | Source video container name |
| `AZURE_STORAGE_OUTPUT_CONTAINER` | Azure upload/deployment | Analysis output container name |
| `AZURE_SEARCH_ENDPOINT` | Search uploads | Search endpoint; infra can generate it when blank |
| `AZURE_SEARCH_INDEX_NAME` | Search uploads | Search index name |

## VIPER UI

| Variable | Required | Description |
| --- | --- | --- |
| `ENABLE_FRONTEND` | Deployment | Set `false` for backend-only deployment |
| `DATABASE_URL` | UI only | PostgreSQL connection string |
| `NEXTAUTH_SECRET` | UI only | Strong random secret for NextAuth |
| `NEXTAUTH_URL` | UI only | Leave blank for Azure; use `http://localhost:3000` for local UI if needed |
| `VIPER_BASE_URL` | Optional | Backend URL for browser/server calls; defaults to local backend in development and backend internal URL in Azure |
| `VIPER_BACKEND_INTERNAL_URL` | Optional | Internal backend URL override |
| `AZ_OPENAI_KEY`, `AZ_OPENAI_BASE`, `AZ_OPENAI_VERSION`, `GPT4` | UI features | Legacy UI OpenAI variables used by UI-specific features |
| `SEARCH_ENDPOINT`, `SEARCH_API_KEY`, `INDEX_NAME` | UI features | UI search variables |

## Keyless authentication

The Python backend prefers Entra ID when service keys are blank. It uses the shared credential helper in `src\cobrapy\azure_credentials.py`:

1. Azure Developer CLI credential
2. Azure CLI credential
3. Managed identity credential

Azure Speech has one extra requirement: when using Entra ID, set `AZURE_SPEECH_RESOURCE_ID` so the SDK can receive tokens in the required `aad#<resourceId>#<token>` format.

## Deployment RBAC

When these resource IDs are set, `azure.yaml` postprovision hooks assign roles to the backend managed identity:

| Variable | Role |
| --- | --- |
| `AZURE_OPENAI_GPT_VISION_RESOURCE_ID` | `Cognitive Services OpenAI User` |
| `AZURE_SPEECH_RESOURCE_ID` | `Cognitive Services User` |

Leave them blank if RBAC is managed outside this deployment.
