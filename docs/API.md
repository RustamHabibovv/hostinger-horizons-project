# API Reference

Complete API documentation for all endpoints.

## Base URL

```
http://localhost:8000
```

## Endpoints Overview

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/health` | Health check and configuration |
| `POST` | `/api/v1/generate` | Single-shot code generation |
| `POST` | `/api/v1/agent/generate` | Multi-agent pipeline |
| `POST` | `/api/v1/react/generate` | ReAct agent |

---

## Health Check

### `GET /health`

Returns server status and configured models.

#### Response

```json
{
  "status": "healthy",
  "models": {
    "simple": "anthropic/claude-sonnet-4",
    "intent": "google/gemini-2.5-flash-lite",
    "planner": "google/gemini-2.5-flash-lite",
    "executor": "anthropic/claude-sonnet-4",
    "react": "anthropic/claude-sonnet-4",
    "embedding": "text-embedding-3-small"
  }
}
```

#### Example

**PowerShell:**
```powershell
Invoke-RestMethod -Uri "http://localhost:8000/health"
```

**curl:**
```bash
curl http://localhost:8000/health
```

---

## Single-Shot Generate

### `POST /api/v1/generate`

Simple, fast code generation with single LLM call.

#### Request Body

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `instruction` | string | Yes | Natural language instruction |
| `project` | string | Yes | Project folder name |

```json
{
  "instruction": "Change the header background color to blue",
  "project": "1-todo-app"
}
```

#### Response (Verbose Mode)

```json
{
  "success": true,
  "message": "Changes applied successfully",
  "files_modified": ["src/components/Header.jsx"],
  "diffs": [
    {
      "filename": "src/components/Header.jsx",
      "diff": "--- a/src/components/Header.jsx\n+++ b/src/components/Header.jsx\n@@ -5,7 +5,7 @@\n   return (\n-    <header className=\"bg-white\">\n+    <header className=\"bg-blue-500\">\n"
    }
  ],
  "total_tokens": 2543,
  "total_duration_ms": 3250
}
```

#### Response (Minimal Mode)

When `AGENT_VERBOSE=false`:

```json
{
  "success": true,
  "diffs": [
    {
      "filename": "src/components/Header.jsx",
      "diff": "--- a/src/components/Header.jsx\n..."
    }
  ]
}
```

#### Example

**PowerShell:**
```powershell
$body = @{
    instruction = "Change the header background color to blue"
    project = "1-todo-app"
} | ConvertTo-Json

Invoke-RestMethod -Uri "http://localhost:8000/api/v1/generate" `
    -Method Post -ContentType "application/json" -Body $body
```

**curl:**
```bash
curl -X POST http://localhost:8000/api/v1/generate \
  -H "Content-Type: application/json" \
  -d '{"instruction": "Change the header background color to blue", "project": "1-todo-app"}'
```

---

## Multi-Agent Generate

### `POST /api/v1/agent/generate`

Multi-step pipeline with intent parsing, retrieval, planning, and validated execution.

#### Request Body

Same as single-shot:

```json
{
  "instruction": "Add a dark mode toggle to the header",
  "project": "1-todo-app"
}
```

#### Response (Verbose Mode)

```json
{
  "success": true,
  "message": "Code generation completed",
  "files_modified": ["src/components/Header.jsx", "src/App.jsx"],
  "diffs": [...],
  "total_tokens": 8542,
  "total_duration_ms": 18500,
  "intent": {
    "type": "feature",
    "complexity": "medium",
    "summary": "Add dark mode toggle button to header"
  },
  "plan": {
    "steps": [
      {"step": 1, "action": "modify", "file": "src/components/Header.jsx"},
      {"step": 2, "action": "modify", "file": "src/App.jsx"}
    ],
    "reasoning": "..."
  },
  "trace": {
    "steps": [
      {"name": "parse_intent", "duration_ms": 1250, "tokens": 659},
      {"name": "retrieve_files", "duration_ms": 830, "tokens": 0},
      {"name": "create_plan", "duration_ms": 2100, "tokens": 2476},
      {"name": "execute", "duration_ms": 14320, "tokens": 5407}
    ]
  }
}
```

#### Response (Minimal Mode)

When `AGENT_VERBOSE=false`:

```json
{
  "success": true,
  "diffs": [...]
}
```

#### Example

**PowerShell:**
```powershell
$body = @{
    instruction = "Add a delete confirmation modal"
    project = "1-todo-app"
} | ConvertTo-Json

Invoke-RestMethod -Uri "http://localhost:8000/api/v1/agent/generate" `
    -Method Post -ContentType "application/json" -Body $body
```

---

## ReAct Agent Generate

### `POST /api/v1/react/generate`

Autonomous agent with reasoning loop and tools.

#### Request Body

Same as other endpoints:

```json
{
  "instruction": "Find all buttons and change their color to red",
  "project": "1-todo-app"
}
```

#### Response (Verbose Mode)

```json
{
  "success": true,
  "message": "Task completed: Changed button colors to red",
  "files_modified": ["src/components/Button.jsx", "src/components/TodoForm.jsx"],
  "diffs": [...],
  "total_tokens": 12450,
  "total_duration_ms": 45000,
  "iterations": 8,
  "trace": [
    {
      "iteration": 1,
      "thought": "I need to find all button components...",
      "action": "search_files",
      "action_input": {"pattern": "button", "in_content": true},
      "observation": "Found 3 files: ..."
    },
    {
      "iteration": 2,
      "thought": "Let me read the Button component...",
      "action": "read_file",
      "action_input": {"file_path": "src/components/Button.jsx"},
      "observation": "[file content]"
    }
  ]
}
```

#### Response (Minimal Mode)

When `AGENT_VERBOSE=false`:

```json
{
  "success": true,
  "diffs": [...]
}
```

#### Example

**PowerShell:**
```powershell
$body = @{
    instruction = "Find all TODO comments and list them"
    project = "1-todo-app"
} | ConvertTo-Json

Invoke-RestMethod -Uri "http://localhost:8000/api/v1/react/generate" `
    -Method Post -ContentType "application/json" -Body $body
```

---

## Error Responses

### Validation Error (400)

```json
{
  "detail": [
    {
      "loc": ["body", "instruction"],
      "msg": "field required",
      "type": "value_error.missing"
    }
  ]
}
```

### Project Not Found (400)

```json
{
  "detail": "Project 'invalid-project' not found"
}
```

### Execution Failed (200 with success=false)

```json
{
  "success": false,
  "message": "Execution failed after 3 attempts",
  "error": "npm run build failed: Module not found..."
}
```

### Server Error (500)

```json
{
  "detail": "Internal server error: [details]"
}
```

---

## Output Formats

Configure via `OUTPUT_FORMAT` and `AGENT_OUTPUT_FORMAT` in `.env`:

### `full_content` (Default)

LLM returns complete file content. Most reliable but uses more tokens.

```json
{
  "modifications": [
    {
      "file": "src/Button.jsx",
      "action": "MODIFY",
      "content": "// Full file content here..."
    }
  ]
}
```

### `search_replace`

LLM returns search/replace blocks. Balanced approach.

```json
{
  "modifications": [
    {
      "file": "src/Button.jsx",
      "action": "MODIFY",
      "search_replace_blocks": [
        {
          "search": "bg-white",
          "replace": "bg-blue-500"
        }
      ]
    }
  ]
}
```

### `diff`

LLM outputs unified diff directly. Fewest tokens but less reliable.

```json
{
  "modifications": [
    {
      "file": "src/Button.jsx",
      "action": "MODIFY",
      "diff": "--- a/src/Button.jsx\n+++ b/src/Button.jsx\n@@ -1,5 +1,5 @@..."
    }
  ]
}
```

---

## Configuration Reference

Set these in `.env` to customize behavior:

### Response Verbosity

```env
AGENT_VERBOSE=true   # Include trace, intent, plan in response
AGENT_VERBOSE=false  # Return only success + diffs (default)
```

### Models

```env
MODEL_SIMPLE=anthropic/claude-sonnet-4       # /generate endpoint
MODEL_INTENT=google/gemini-2.5-flash-lite    # Intent parsing
MODEL_PLANNER=google/gemini-2.5-flash-lite   # Planning
MODEL_EXECUTOR=anthropic/claude-sonnet-4     # Code generation
MODEL_REACT=anthropic/claude-sonnet-4        # ReAct agent
```

### Agent Settings

```env
AGENT_MAX_RETRIES=3            # Retry on build failure
AGENT_RETRIEVAL_TOP_K=5        # Files to retrieve
AGENT_VALIDATE_BUILD=true      # Run npm build
REACT_MAX_ITERATIONS=15        # Max agent loops
```

See [.env.example](../backend/.env.example) for all options.
