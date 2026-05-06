# Content Based Image Retrieval Analysis (a.k.a. COBRA)

A python library that illustrates how to do "Content Based image Retrieval Analysis", a technique for prompting vision-enabled LLMs to extract details from video and image content.

## Getting Started

1. Clone the repository to your device.
Navigate to the repository directory and install dependencies:

```bash
cd /path/to/local/repo/cobrapy
 pip install -e . #"." means install at the current location
```

2. Follow the official instructions to download and install FFmpeg:
[https://ffmpeg.org/download.html](https://ffmpeg.org/download.html)

3. Copy `sample.env` to `.env` at the repository root and populate it with your service settings. The backend and UI components both consume environment variables from this shared file. Run `python scripts/apply_database_url.py` to materialize the local `DATABASE_URL` entry using the shared values in `config/database_urls.json`; the same configuration file provides the cloud connection string used by the container setup and deployment scripts.

For local video validation with real Azure Speech and Azure OpenAI services, see [LOCAL_TESTING.md](LOCAL_TESTING.md).

4. Start the FastAPI backend on port 8000:

```bash
poetry install
poetry run uvicorn cobrapy.api.app:app --host 0.0.0.0 --port 8000
```

5. In a separate terminal, install the UI dependencies and start the Next.js frontend on port 3000:

```bash
cd src/ui
npm install
npm run dev
```

The UI automatically proxies requests to `http://localhost:8000`, so no additional environment variables are required to wire the services together.

## Deploy to Azure with Azure Developer CLI (azd)

The fastest way to deploy VIPER to Azure is using the [Azure Developer CLI](https://learn.microsoft.com/azure/developer/azure-developer-cli/overview) (`azd`).

### Prerequisites

- [Azure Developer CLI](https://learn.microsoft.com/azure/developer/azure-developer-cli/install-azd)
- [Docker](https://www.docker.com/products/docker-desktop)
- An Azure subscription

### Quick Start

1. Copy `sample.env` to `.env` and populate it with your Azure service settings:

   ```bash
   cp sample.env .env
   # Edit .env with your Azure OpenAI, Speech, Storage, and Search settings
   ```

   Load the `.env` values into the selected azd environment before provisioning:

   ```bash
   azd env set --file .env
   ```

2. Initialize the azd environment (first time only):

   ```bash
   azd init
   ```

3. Provision infrastructure and deploy:

   ```bash
   azd up
   ```

   This command will:
   - Create or update Azure resources (Container Registry, Container Apps, Storage, and Search)
   - Build and push Docker images for the backend and frontend
   - Deploy the containers with environment variables from your `.env` file

For a backend-only COBRA API deployment, set `ENABLE_FRONTEND="false"` in `.env`, then run:

```powershell
azd env set --file .env
azd provision
azd deploy backend
```

### Environment Variables

The deployment inherits configuration from your `.env` file. Key variables include:

| Variable | Description |
|----------|-------------|
| `AZURE_OPENAI_GPT_VISION_ENDPOINT` | Azure OpenAI endpoint for vision analysis |
| `AZURE_OPENAI_GPT_VISION_API_KEY` | Optional API key for Azure OpenAI; leave blank for Entra ID |
| `AZURE_OPENAI_GPT_VISION_RESOURCE_ID` | Azure OpenAI or AI Services resource ID for deployment-time managed identity RBAC |
| `AZURE_SPEECH_REGION` | Azure Speech Services region |
| `AZURE_SPEECH_RESOURCE_ID` | Azure Speech or AI Services resource ID for Entra ID authentication |
| `AZURE_STORAGE_ACCOUNT_URL` | Blob storage URL for videos (auto-generated if not provided) |
| `AZURE_SEARCH_ENDPOINT` | Azure AI Search endpoint (auto-generated if not provided) |
| `ENABLE_FRONTEND` | Set to `false` for backend-only COBRA API deployment |
| `DATABASE_URL` | PostgreSQL connection string for the UI; required only when deploying the frontend |

See `sample.env` for the complete list of configuration options.

### Manual Deployment

For more control over the deployment process, use the PowerShell script:

```powershell
./scripts/Deploy-ViperToAzure.ps1 `
    -SubscriptionId "your-subscription-id" `
    -ResourceGroupName "viper-prod" `
    -Location "eastus"
```

See `azure/README.md` for detailed deployment options.

## Contributing

This project welcomes contributions and suggestions. Most contributions require you to agree to a
Contributor License Agreement (CLA) declaring that you have the right to, and actually do, grant us
the rights to use your contribution. For details, visit https://cla.opensource.microsoft.com.

When you submit a pull request, a CLA bot will automatically determine whether you need to provide
a CLA and decorate the PR appropriately (e.g., status check, comment). Simply follow the instructions
provided by the bot. You will only need to do this once across all repos using our CLA.

This project has adopted the [Microsoft Open Source Code of Conduct](https://opensource.microsoft.com/codeofconduct/).
For more information see the [Code of Conduct FAQ](https://opensource.microsoft.com/codeofconduct/faq/) or
contact [opencode@microsoft.com](mailto:opencode@microsoft.com) with any additional questions or comments.

## Trademarks

This project may contain trademarks or logos for projects, products, or services. Authorized use of Microsoft
trademarks or logos is subject to and must follow
[Microsoft's Trademark & Brand Guidelines](https://www.microsoft.com/en-us/legal/intellectualproperty/trademarks/usage/general).
Use of Microsoft trademarks or logos in modified versions of this project must not cause confusion or imply Microsoft sponsorship.
Any use of third-party trademarks or logos are subject to those third-party's policies.
