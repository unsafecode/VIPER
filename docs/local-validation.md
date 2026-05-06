# Local COBRA video validation

Use local validation when Azure OpenAI/Azure AI Services and Speech resources already exist and you want the easier path for testing a single MP4. If those resources do not exist yet, start with [Azure deployment](azure-deployment.md). The flow still uses real preprocessing, real Azure OpenAI, and real Azure Speech when transcripts are enabled.

## Prerequisites

- Python 3.11
- FFmpeg on `PATH`; both `ffmpeg` and `ffprobe` must work
- Azure OpenAI or Azure AI Services resource with a chat/vision-capable deployment
- Azure Speech-capable resource when transcript generation is enabled
- Local Azure authentication for the tenant that owns those resources

Verify FFmpeg:

```powershell
ffmpeg -version
ffprobe -version
```

Install the Python package from the repository root:

```powershell
python -m pip install -e .
```

## Azure login

Use tenant-isolated Azure configuration when multiple tenants or subscriptions are in use:

```powershell
$env:AZURE_CONFIG_DIR = "C:\Users\<you>\.azure-tenants\<alias>"
$env:AZD_CONFIG_DIR = "C:\Users\<you>\.azd-tenants\<alias>"
az login --tenant "<tenant-id>"
az account set --subscription "<subscription-name-or-id>"
az account show --query "{subscription:name, tenant:tenantId}" -o table
azd auth login
```

## Configure `.env`

Copy `sample.env` to `.env` and set these values, or hydrate `.env` from the selected azd environment after deployment/provisioning:

```powershell
azd env get-values |
  Where-Object { $_ -match '^[A-Za-z_][A-Za-z0-9_]*=' } |
  Set-Content .env
```

This overwrites the local `.env` file. Leave API keys blank for keyless auth.

```text
AZURE_OPENAI_GPT_VISION_API_KEY=""
AZURE_OPENAI_GPT_VISION_ENDPOINT="https://<resource>.cognitiveservices.azure.com/"
AZURE_OPENAI_GPT_VISION_API_VERSION="<api-version>"
AZURE_OPENAI_GPT_VISION_DEPLOYMENT="<deployment-name>"

AZURE_SPEECH_REGION="<region>"
AZURE_SPEECH_USE_MANAGED_IDENTITY="true"
AZURE_SPEECH_RESOURCE_ID="/subscriptions/<subscription-id>/resourceGroups/<resource-group>/providers/Microsoft.CognitiveServices/accounts/<resource-name>"
```

Speech values are required only when transcript generation is enabled. Storage and Search variables are optional for local-only testing because the script uses `VideoClient(upload_to_azure=False)`.

## Run with the command-line script

```powershell
python scripts\run_local_video_analysis.py "C:\path\to\video.mp4" `
  --output-dir outputs\my-video `
  --segment-length 10 `
  --fps 0.5
```

Skip Speech transcription:

```powershell
python scripts\run_local_video_analysis.py "C:\path\to\video.mp4" --no-transcripts
```

Choose the analysis type:

```powershell
python scripts\run_local_video_analysis.py "C:\path\to\video.mp4" --analysis action-summary
python scripts\run_local_video_analysis.py "C:\path\to\video.mp4" --analysis chapter-analysis
```

The script prints a JSON summary with:

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
- `_ActionSummary.json` or `_ChapterAnalysis.json`
- per-segment folders with extracted frames
- per-segment prompt JSON files
- extracted WAV audio when audio is present
- transcript details in the manifest when transcription is enabled and speech is recognized

Generated outputs should stay under `outputs/` or `samples/local-test/`; both are ignored by git.

## Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| `Video file not found` | Incorrect local path | Use an absolute Windows path or run from the repository root |
| FFmpeg errors | FFmpeg is missing or cannot decode the input | Confirm `ffmpeg -version`, then try another MP4 or re-encode the file |
| Speech returns no transcript | No recognizable speech, wrong language, or Speech auth/config issue | Confirm `AZURE_SPEECH_REGION`, `AZURE_SPEECH_RESOURCE_ID`, and `AZURE_SPEECH_USE_MANAGED_IDENTITY=true`; test with a short clip containing clear speech |
| Azure OpenAI auth fails | Not logged into the right tenant/subscription or missing RBAC | Re-run tenant-isolated Azure login and confirm access to the target resource |
| Model rejects the request | Deployment/API version mismatch | Check `AZURE_OPENAI_GPT_VISION_API_VERSION` and `AZURE_OPENAI_GPT_VISION_DEPLOYMENT` |
| Outputs disappear when using custom code | `VideoClient.analyze_video()` performs cleanup | Use the provided script or call `client.analyzer.analyze_video(...)` when you need to preserve local artifacts |
