# Local COBRA video testing

Use this guide to validate COBRA locally on an MP4 file before deploying the backend API. The flow uses real preprocessing, real Azure Speech transcription, and real Azure OpenAI analysis. It does not use stubs or fake model responses.

## Prerequisites

- Python 3.11.
- FFmpeg on `PATH`; both `ffmpeg` and `ffprobe` must work.
- Access to an Azure AI Services or Azure OpenAI resource with a chat/vision-capable deployment.
- Access to an Azure Speech-capable resource.
- Local Azure authentication for the tenant that owns those resources.

On Windows, verify FFmpeg:

```powershell
ffmpeg -version
ffprobe -version
```

Install the Python package from the repo root:

```powershell
python -m pip install -e .
```

## Azure login

Use tenant-isolated Azure configuration when multiple tenants or subscriptions are in use. Set these variables before running Azure commands or local tests:

```powershell
$env:AZURE_CONFIG_DIR = "C:\Users\<you>\.azure-tenants\<alias>"
$env:AZD_CONFIG_DIR = "C:\Users\<you>\.azd-tenants\<alias>"
az login --tenant "<tenant-id>"
az account set --subscription "<subscription-name-or-id>"
az account show --query "{subscription:name, tenant:tenantId}" -o table
```

If you use Azure Developer CLI credentials locally, authenticate azd in the same isolated context:

```powershell
azd auth login
```

## Configure `.env`

Copy `sample.env` to `.env` and set these values. Leave API keys blank for the keyless path. Speech values are required when transcript generation is enabled.

```text
AZURE_OPENAI_GPT_VISION_API_KEY=""
AZURE_OPENAI_GPT_VISION_ENDPOINT="https://<resource>.cognitiveservices.azure.com/"
AZURE_OPENAI_GPT_VISION_API_VERSION="2025-04-01-preview"
AZURE_OPENAI_GPT_VISION_DEPLOYMENT="<deployment-name>"

AZURE_SPEECH_REGION="<region>"
AZURE_SPEECH_USE_MANAGED_IDENTITY="true"
AZURE_SPEECH_RESOURCE_ID="/subscriptions/<subscription-id>/resourceGroups/<resource-group>/providers/Microsoft.CognitiveServices/accounts/<resource-name>"
```

Storage and Search variables are optional for local-only testing. The local test script uses `VideoClient(upload_to_azure=False)` and writes outputs to disk.

## Run with the command-line script

From the repo root:

```powershell
python scripts\run_local_video_analysis.py "C:\path\to\video.mp4" `
  --output-dir outputs\my-video `
  --segment-length 10 `
  --fps 0.5
```

To skip Speech transcription:

```powershell
python scripts\run_local_video_analysis.py "C:\path\to\video.mp4" --no-transcripts
```

The script prints a JSON summary that includes:

- manifest path
- output directory
- extracted WAV path
- transcript segment count and preview
- video segment count
- analysis output path
- analysis entry count

## Run with the notebook

Open `samples\cobra_sample_usage.ipynb` and update:

- `VIDEO_PATH`
- `OUTPUT_DIR`
- `SEGMENT_LENGTH_SECONDS`
- `FRAMES_PER_SECOND`
- `GENERATE_TRANSCRIPTS`

Then run the notebook cells in order.

## Expected outputs

The output directory contains:

- `_video_manifest.json`
- `_ActionSummary.json`
- per-segment folders with extracted frames
- per-segment prompt JSON files
- extracted WAV audio when audio is present
- transcript details in the manifest when transcription is enabled and speech is recognized

## Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| `Video file not found` | Incorrect local path | Use an absolute Windows path or run from the repo root. |
| FFmpeg errors | FFmpeg is missing or cannot decode the input | Confirm `ffmpeg -version`, then try another MP4 or re-encode the file. |
| Speech returns no transcript | No recognizable speech, wrong language, or Speech auth/config issue | Confirm `AZURE_SPEECH_REGION`, `AZURE_SPEECH_RESOURCE_ID`, and `AZURE_SPEECH_USE_MANAGED_IDENTITY=true`; test with a short clip containing clear speech. |
| Azure OpenAI auth fails | Not logged into the right tenant/subscription or missing RBAC | Re-run the tenant-isolated Azure login commands and confirm access to the target resource. |
| Model rejects the request | Deployment/API version mismatch | Check `AZURE_OPENAI_GPT_VISION_API_VERSION` and `AZURE_OPENAI_GPT_VISION_DEPLOYMENT`. |
| Outputs disappear when using custom code | `VideoClient.analyze_video()` performs cleanup | Use the provided script or call `client.analyzer.analyze_video(...)` when you need to preserve local artifacts. |
