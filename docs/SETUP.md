# Setup Guide

Complete instructions for setting up the Hostinger Horizons AI Code Editor locally.

## Prerequisites

| Requirement | Version | Check Command |
|-------------|---------|---------------|
| Python | 3.11+ | `python --version` |
| Node.js | 18+ | `node --version` |
| npm | 9+ | `npm --version` |
| Git | Any | `git --version` |

## Step 1: Clone the Repository

```bash
git clone <repository-url>
cd hostinger-horizons-project
```

## Step 2: Backend Setup

### Create Virtual Environment

**Windows (PowerShell):**
```powershell
cd backend
python -m venv venv
.\venv\Scripts\activate
```

**macOS/Linux:**
```bash
cd backend
python3 -m venv venv
source venv/bin/activate
```

### Install Python Dependencies

```bash
pip install -r requirements.txt
```

This installs:
- `fastapi` — Web framework
- `uvicorn` — ASGI server
- `openai` — LLM API client
- `faiss-cpu` — Vector search
- `pydantic-settings` — Configuration
- `tiktoken` — Token counting
- `numpy` — Array operations

## Step 3: Configure Environment

```bash
cp .env.example .env
```

Edit `.env` with your configuration:

### Required: API Key

Get an API key from one of these providers:

| Provider | URL | Recommendation |
|----------|-----|----------------|
| **OpenRouter** | https://openrouter.ai/keys | Recommended — access 100+ models |
| OpenAI | https://platform.openai.com/api-keys | Direct OpenAI access |

```env
LLM_API_KEY=your-api-key-here
LLM_BASE_URL=https://openrouter.ai/api/v1
```

### Optional: Model Configuration

```env
# Use cheaper models for parsing/planning, powerful for code generation
MODEL_INTENT=google/gemini-2.5-flash-lite
MODEL_PLANNER=google/gemini-2.5-flash-lite
MODEL_EXECUTOR=anthropic/claude-sonnet-4
MODEL_REACT=anthropic/claude-sonnet-4
```

See [.env.example](../backend/.env.example) for all configuration options.

## Step 4: Sample Project Setup

Clone the sample React projects (created with Hostinger Horizons):

```bash
cd hostinger-horizons-project
git clone https://github.com/tomasrasymas/sample-react-projects.git
```

Install npm dependencies for each project you want to test:

```bash
cd sample-react-projects

# Todo App (recommended for initial testing)
cd 1-todo-app
npm install
cd ..

# Other projects (optional)
cd 2-candy-pop-landing && npm install && cd ..
cd 3-qr-generator && npm install && cd ..
```

**Note:** The agent uses `npm run build` for validation, so dependencies must be installed.

## Step 5: Start the Server

```bash
cd backend
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

You should see:
```
INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)
INFO:     Started reloader process
```

## Step 6: Verify Installation

### Health Check

```powershell
Invoke-RestMethod -Uri "http://localhost:8000/health"
```

Expected response:
```json
{
  "status": "healthy",
  "models": {
    "simple": "anthropic/claude-sonnet-4",
    "intent": "google/gemini-2.5-flash-lite",
    ...
  }
}
```

### First API Call

**Simple endpoint:**
```powershell
$body = @{
    instruction = "change the header background color to blue"
    project = "1-todo-app"
} | ConvertTo-Json

Invoke-RestMethod -Uri "http://localhost:8000/api/v1/generate" `
    -Method Post -ContentType "application/json" -Body $body
```

**Using curl:**
```bash
curl -X POST http://localhost:8000/api/v1/generate \
  -H "Content-Type: application/json" \
  -d '{"instruction": "change the header background color to blue", "project": "1-todo-app"}'
```

## Troubleshooting

### "API key not configured"

**Problem:** Missing or invalid API key.

**Solution:** Ensure `.env` file exists and contains valid `LLM_API_KEY`:
```bash
cat backend/.env | grep LLM_API_KEY
```

### "Project not found"

**Problem:** Invalid project name or path.

**Solution:** Check available projects:
```bash
ls sample-react-projects/
```

Use the folder name (e.g., `1-todo-app`, not `Todo App`).

### "npm run build failed"

**Problem:** Dependencies not installed in sample project.

**Solution:**
```bash
cd sample-react-projects/1-todo-app
npm install
```

### "Module not found: faiss"

**Problem:** FAISS installation issues on Windows.

**Solution:** Use CPU version:
```bash
pip uninstall faiss-gpu
pip install faiss-cpu
```

### "Connection refused"

**Problem:** Server not running or wrong port.

**Solution:**
1. Check server is running: `uvicorn app.main:app --reload`
2. Check port: Default is 8000
3. Check firewall settings

### Import Errors

**Problem:** Missing Python dependencies.

**Solution:**
```bash
pip install -r requirements.txt --force-reinstall
```

## Next Steps

- Try the [API endpoints](API.md) with different instructions
- Learn about the [three approaches](APPROACHES.md)
- Understand the [architecture](ARCHITECTURE.md)
