# Architecture

System design and component overview for the Hostinger Horizons AI Code Editor.

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              FastAPI Backend                                 │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌──────────────┐   ┌──────────────┐   ┌──────────────┐                    │
│  │   /generate  │   │ /agent/gen   │   │ /react/gen   │    API Layer       │
│  │  (Simple)    │   │ (Pipeline)   │   │ (ReAct)      │                    │
│  └──────┬───────┘   └──────┬───────┘   └──────┬───────┘                    │
│         │                  │                  │                             │
│         ▼                  ▼                  ▼                             │
│  ┌──────────────┐   ┌──────────────────────────────────┐                   │
│  │  LLM Service │   │         Agent Services           │    Service Layer  │
│  │              │   │  ┌────────┬────────┬──────────┐  │                   │
│  │  - generate  │   │  │ Intent │Planner │ Executor │  │                   │
│  │  - parse     │   │  └────────┴────────┴──────────┘  │                   │
│  └──────┬───────┘   │  ┌────────────────────────────┐  │                   │
│         │           │  │      ReAct Loop + Tools    │  │                   │
│         │           │  └────────────────────────────┘  │                   │
│         │           └──────────────┬───────────────────┘                   │
│         │                          │                                        │
│         ▼                          ▼                                        │
│  ┌────────────────────────────────────────────────────────────────┐        │
│  │                      Shared Services                           │        │
│  │  ┌───────────┐  ┌───────────┐  ┌───────────┐  ┌───────────┐   │        │
│  │  │Embeddings │  │ Retrieval │  │   Diff    │  │ LLM Client│   │        │
│  │  │  (FAISS)  │  │ (Multi-   │  │ (difflib, │  │ (OpenAI   │   │        │
│  │  │           │  │  signal)  │  │ git apply)│  │  SDK)     │   │        │
│  │  └───────────┘  └───────────┘  └───────────┘  └───────────┘   │        │
│  └────────────────────────────────────────────────────────────────┘        │
│                                                                              │
└─────────────────────────┬───────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         External Dependencies                                │
│  ┌───────────────────┐  ┌───────────────────┐  ┌───────────────────┐       │
│  │   LLM Provider    │  │  Sample React     │  │     node_modules  │       │
│  │ (OpenRouter/OpenAI)│  │    Projects       │  │   (for validation)│       │
│  └───────────────────┘  └───────────────────┘  └───────────────────┘       │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Component Overview

### API Layer (`app/api/`)

| File | Endpoint | Purpose |
|------|----------|---------|
| `routes.py` | `/api/v1/generate` | Simple single-shot generation |
| `agent_routes.py` | `/api/v1/agent/generate` | Multi-agent pipeline |
| `react_routes.py` | `/api/v1/react/generate` | ReAct agent loop |

All endpoints share the same request format:
```python
class GenerateRequest:
    instruction: str  # Natural language instruction
    project: str      # Project folder name
```

### Service Layer

#### LLM Service (`services/llm.py`)

Central LLM interaction with support for multiple output strategies:

```python
# Three output format strategies:
- full_content  # LLM returns complete file → difflib generates diff
- search_replace  # LLM returns SEARCH/REPLACE blocks → apply → diff
- diff  # LLM outputs unified diff directly
```

#### Embeddings Service (`services/embeddings.py`)

Vector-based semantic search using FAISS:

```python
# Flow:
1. Index all project files → text-embedding-3-small → FAISS index
2. Store index with content hash (cache invalidation)
3. Query: instruction → embedding → top-k similar files
```

#### Retrieval Service (`services/retrieval.py`)

Multi-signal file ranking combining:

| Signal | Weight | Description |
|--------|--------|-------------|
| Semantic | 0.5 | Cosine similarity from embeddings |
| Keyword | 0.3 | PascalCase, camelCase, UI terms matching |
| Hint | 0.8 | File/component hints from intent parsing |

#### Diff Service (`services/diff.py`)

Unified diff generation and application:

```python
# Flow:
1. Generate unified diff (difflib.unified_diff)
2. Create backup with timestamp
3. Apply via `git apply --3way`
4. Fallback to direct write if patch fails
```

### Agent Services (`services/agent/`)

#### Intent Parser (`agent/intent.py`)

Classifies user instruction and extracts signals:

```python
@dataclass
class ParsedIntent:
    intent_type: str      # feature, bugfix, refactor, style, docs
    complexity: str       # low, medium, high
    summary: str          # Brief description
    file_hints: list      # Suggested files (*.css, Button.jsx)
    component_hints: list # Component names
    keywords: list        # Search keywords
    confidence: float     # 0.0 - 1.0
```

#### Planner (`agent/planner.py`)

Creates execution plan from intent and retrieved files:

```python
@dataclass
class ExecutionPlan:
    steps: list[Step]       # Ordered modification steps
    files_to_modify: list   # Existing files to change
    files_to_create: list   # New files to create
    reasoning: str          # Explanation of plan
```

#### Executor (`agent/executor.py`)

Generates code with retry loop:

```
┌─────────────┐
│   Start     │
└──────┬──────┘
       ▼
┌─────────────┐     ┌─────────────┐
│ Generate    │────►│  Validate   │
│   Code      │     │  Imports    │
└─────────────┘     └──────┬──────┘
                           │
              ┌────────────┼────────────┐
              ▼            │            ▼
        ┌─────────┐        │      ┌─────────┐
        │  Pass   │        │      │  Fail   │
        └────┬────┘        │      └────┬────┘
             │             │           │
             ▼             │           ▼
       ┌──────────┐        │    ┌──────────┐
       │ npm build│        │    │  Retry   │◄──┐
       │ validate │        │    │  with    │   │
       └────┬─────┘        │    │ feedback │───┘
            │              │    └──────────┘
   ┌────────┼────────┐     │     (max 3x)
   ▼        │        ▼     │
┌──────┐    │    ┌──────┐  │
│ Pass │    │    │ Fail │──┘
└──┬───┘    │    └──────┘
   │        │
   ▼        │
┌──────────────┐
│   Success    │
│ Apply Diffs  │
└──────────────┘
```

### ReAct Agent (`services/react_agent/`)

#### Loop (`react_agent/loop.py`)

Implements Thought-Action-Observation cycle:

```
┌─────────────────────────────────────────┐
│              ReAct Loop                  │
│                                          │
│  ┌──────────────────────────────────┐   │
│  │           THOUGHT                 │   │
│  │  "I need to find the button..."   │   │
│  └─────────────┬────────────────────┘   │
│                │                         │
│                ▼                         │
│  ┌──────────────────────────────────┐   │
│  │           ACTION                  │   │
│  │  search_files("Button")          │   │
│  └─────────────┬────────────────────┘   │
│                │                         │
│                ▼                         │
│  ┌──────────────────────────────────┐   │
│  │         OBSERVATION               │   │
│  │  Found: src/components/Button.jsx │   │
│  └─────────────┬────────────────────┘   │
│                │                         │
│                ▼                         │
│         Repeat until finish              │
└─────────────────────────────────────────┘
```

#### Tools (`react_agent/tools.py`)

| Category | Tools | Purpose |
|----------|-------|---------|
| **Exploration** | `search_files`, `list_directory`, `semantic_search` | Discover project structure |
| **Reading** | `read_file`, `list_dependencies` | Understand existing code |
| **Writing** | `write_file`, `edit_file` | Stage changes |
| **Validation** | `validate_changes`, `run_eslint` | Check before applying |
| **Control** | `apply_changes`, `finish` | Finalize task |

## Data Flow

### Single-Shot (`/api/v1/generate`)

```
Request → Read All Files → LLM Generate → Parse Response → Generate Diff → Apply → Response
```

### Multi-Agent (`/api/v1/agent/generate`)

```
Request → Intent Parse → Embed & Retrieve → Plan → Execute (retry loop) → Validate Build → Response
           (cheap model)    (embeddings)    (cheap)   (powerful model)      (npm build)
```

### ReAct (`/api/v1/react/generate`)

```
Request → Initialize Tools → Loop(Thought → Action → Observation) → Apply Changes → Response
                                        ↑_______________|
                                      (until finish tool)
```

## Configuration System

Uses Pydantic Settings with cascading configuration:

```
Defaults (config.py) → .env file → Environment variables
```

All settings are validated at startup and available via `get_settings()`.

## Error Handling

| Layer | Strategy |
|-------|----------|
| API | HTTPException with status codes |
| Executor | Retry with error feedback to LLM |
| Validation | Classify error type (syntax, import, type, build) |
| Diff | Backup before changes, revert on failure |

## Caching

| What | Where | Invalidation |
|------|-------|--------------|
| Project embeddings | `{project}/.faiss_index/` | Content hash mismatch |
| Settings | LRU cache | Application restart |

## Security Considerations

- API key stored in `.env` (gitignored)
- No authentication on endpoints (local development only)
- File operations restricted to `sample-react-projects/`
- Subprocess commands use explicit paths
