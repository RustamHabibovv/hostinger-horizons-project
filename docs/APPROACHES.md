# Three Approaches to AI Code Generation

This document provides a deep dive into the three distinct approaches implemented for code generation, their design rationale, and when to use each.

## Overview Comparison

| Aspect | Single-Shot | Multi-Agent | ReAct Agent |
|--------|-------------|-------------|-------------|
| **Complexity** | Low | Medium | High |
| **API Calls** | 1 | 3-6 | 5-50+ |
| **Latency** | Fast (2-5s) | Medium (10-30s) | Variable (15-120s) |
| **Cost** | Low | Optimized | Higher |
| **Reliability** | Context-dependent | High (validation) | Self-correcting |
| **Best For** | Simple changes | Production changes | Exploration |

---

## Approach 1: Single-Shot Prompt

### Endpoint
`POST /api/v1/generate`

### Design Philosophy
The simplest approach: provide the LLM with all project context and the instruction, let it figure out what to change.

### Flow

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│   Request    │────►│  Read All    │────►│  LLM Call    │
│              │     │    Files     │     │  (generate)  │
└──────────────┘     └──────────────┘     └──────┬───────┘
                                                  │
                                                  ▼
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│   Response   │◄────│  Apply Diff  │◄────│ Parse & Diff │
│              │     │              │     │              │
└──────────────┘     └──────────────┘     └──────────────┘
```

### Implementation Details

1. **Read all project files** — Recursively read `.js`, `.jsx`, `.ts`, `.tsx`, `.css` files
2. **Build context** — Format files with paths and content
3. **Call LLM** — Single request with system prompt + context + instruction
4. **Parse response** — Extract modifications (supports 3 output formats)
5. **Generate diff** — Use difflib for unified diff
6. **Apply changes** — Use `git apply` for atomic application

### Output Format Strategies

| Format | Token Usage | Reliability | Best For |
|--------|-------------|-------------|----------|
| `full_content` | Highest | Most reliable | Default choice |
| `search_replace` | Medium | Good | Surgical edits |
| `diff` | Lowest | Least reliable | Cost optimization |

### Advantages

- ✅ **Simple** — Easy to understand and debug
- ✅ **Fast** — Single API call
- ✅ **Predictable** — Same flow every time
- ✅ **Low latency** — 2-5 seconds typical

### Disadvantages

- ❌ **Context limit** — Large projects may exceed token limits
- ❌ **No validation** — Errors only caught when applying
- ❌ **No retry** — Single attempt
- ❌ **All-or-nothing** — Must send entire project context

### When to Use

- Small to medium projects (< 50 files)
- Simple, isolated changes
- Quick iterations during development
- Cost-sensitive scenarios

---

## Approach 2: Multi-Agent Pipeline

### Endpoint
`POST /api/v1/agent/generate`

### Design Philosophy
Decompose the problem into specialized stages, each handled by the most appropriate (and cost-effective) model. Include validation to ensure correctness.

### Pipeline Stages

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         Multi-Agent Pipeline                                 │
│                                                                              │
│  ┌────────────┐   ┌────────────┐   ┌────────────┐   ┌────────────┐         │
│  │   INTENT   │──►│ RETRIEVAL  │──►│  PLANNER   │──►│  EXECUTOR  │         │
│  │   PARSE    │   │            │   │            │   │   (retry)  │         │
│  │            │   │            │   │            │   │            │         │
│  │ model:cheap│   │ embeddings │   │ model:cheap│   │model:best  │         │
│  └────────────┘   └────────────┘   └────────────┘   └─────┬──────┘         │
│                                                            │                │
│                                                            ▼                │
│                                                     ┌────────────┐         │
│                                                     │  VALIDATE  │         │
│                                                     │ npm build  │         │
│                                                     └────────────┘         │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Stage 1: Intent Parsing

**Purpose:** Classify the instruction and extract signals for retrieval.

**Model:** Cheap/fast (e.g., `gemini-2.5-flash-lite`)

**Output:**
```json
{
  "intent_type": "style",
  "complexity": "low",
  "file_hints": ["*.css", "Button.jsx"],
  "component_hints": ["Button", ".header"],
  "keywords": ["button", "color", "red"]
}
```

### Stage 2: Retrieval

**Purpose:** Find the most relevant files without sending everything.

**Method:** Multi-signal ranking

| Signal | Weight | Source |
|--------|--------|--------|
| Semantic | 0.5 | FAISS embeddings (cosine similarity) |
| Keyword | 0.3 | Pattern matching (PascalCase, camelCase) |
| Hint | 0.8 | Intent parser file/component hints |

**Output:** Top-K most relevant files (default: 5)

### Stage 3: Planning

**Purpose:** Create a structured execution plan.

**Model:** Cheap/fast (same as intent)

**Output:**
```json
{
  "steps": [
    {"step": 1, "action": "modify", "file": "src/Button.jsx", "description": "..."},
    {"step": 2, "action": "create", "file": "src/utils/colors.js", "description": "..."}
  ],
  "files_to_modify": ["src/Button.jsx"],
  "files_to_create": ["src/utils/colors.js"],
  "reasoning": "..."
}
```

### Stage 4: Execution

**Purpose:** Generate the actual code changes.

**Model:** Best quality (e.g., `claude-sonnet-4`)

**Features:**
- Retry loop (max 3 attempts by default)
- Error feedback to LLM on failure
- Import validation before build

### Stage 5: Validation

**Purpose:** Ensure changes don't break the build.

**Method:** Run `npm run build` and check exit code

**On Failure:** Feed error output back to executor for retry

### Validation Checks

| Check | When | Purpose |
|-------|------|---------|
| Required files created | Before build | Ensure plan followed |
| Relative imports valid | Before build | Paths to existing files |
| NPM packages exist | Before build | No undefined dependencies |
| Build succeeds | After write | Syntax and type errors |

### Cost Optimization

By using cheaper models for parsing and planning, the expensive model is only used for actual code generation:

```
Intent:   ~500 tokens   × cheap model  = $0.0001
Planner:  ~1,500 tokens × cheap model  = $0.0003
Executor: ~5,000 tokens × best model   = $0.01
Total:    ~7,000 tokens               ≈ $0.01

vs. Single-shot with best model:
~10,000 tokens × best model           ≈ $0.02
```

### Advantages

- ✅ **Cost-optimized** — Right model for each task
- ✅ **Validated** — Build check catches errors
- ✅ **Retry logic** — Self-correction on failure
- ✅ **Focused context** — Only relevant files sent
- ✅ **Predictable** — Structured pipeline

### Disadvantages

- ❌ **More complex** — Multiple stages to debug
- ❌ **Higher latency** — 10-30 seconds
- ❌ **Fixed pipeline** — Can't adapt mid-execution
- ❌ **Retrieval dependent** — Wrong files = wrong output

### When to Use

- Production-quality changes
- When validation is important
- Cost-sensitive with quality requirements
- Medium complexity tasks

---

## Approach 3: ReAct Agent

### Endpoint
`POST /api/v1/react/generate`

### Design Philosophy
Give the LLM tools and let it autonomously explore, reason, and act until the task is complete. Based on the [ReAct paper](https://arxiv.org/abs/2210.03629) (Reason + Act).

### Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                            ReAct Loop                                        │
│                                                                              │
│   ┌──────────────────────────────────────────────────────────────────────┐  │
│   │                                                                      │  │
│   │  ┌──────────┐     ┌──────────┐     ┌──────────┐                    │  │
│   │  │ THOUGHT  │────►│  ACTION  │────►│OBSERVATION│───┐               │  │
│   │  │          │     │          │     │          │   │               │  │
│   │  │ "I need  │     │ search_  │     │ Found:   │   │               │  │
│   │  │  to find │     │ files()  │     │ Button.  │   │               │  │
│   │  │  the..." │     │          │     │ jsx      │   │               │  │
│   │  └──────────┘     └──────────┘     └──────────┘   │               │  │
│   │       ▲                                           │               │  │
│   │       └───────────────────────────────────────────┘               │  │
│   │                     (repeat until finish)                          │  │
│   └──────────────────────────────────────────────────────────────────────┘  │
│                                                                              │
│   When ACTION = finish:                                                      │
│   ┌──────────────────────────────────────────────────────────────────────┐  │
│   │  Apply pending changes → Generate diffs → Return response            │  │
│   └──────────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Available Tools

| Tool | Parameters | Purpose |
|------|------------|---------|
| `search_files` | `pattern`, `in_content` | Regex search for files |
| `read_file` | `file_path`, `start_line`, `end_line` | Read file content |
| `list_directory` | `path` | Explore project structure |
| `semantic_search` | `query`, `top_k` | Embeddings-based search |
| `list_dependencies` | - | List npm packages |
| `write_file` | `file_path`, `content` | Create/overwrite file |
| `edit_file` | `file_path`, `old_content`, `new_content` | Surgical edit |
| `validate_changes` | - | Check pending changes |
| `run_eslint` | `file_path` | Lint check |
| `apply_changes` | - | Write pending files |
| `finish` | `summary` | Complete task |

### Example Trace

```
THOUGHT: I need to find where the header component is defined.
ACTION: search_files {"pattern": "Header", "in_content": false}
OBSERVATION: Found 2 files: src/components/Header.jsx, src/layouts/Header.jsx

THOUGHT: The main header is likely in components. Let me read it.
ACTION: read_file {"file_path": "src/components/Header.jsx"}
OBSERVATION: [file content with className="bg-white"...]

THOUGHT: I found the header. I need to change bg-white to bg-blue-500.
ACTION: edit_file {"file_path": "src/components/Header.jsx", "old_content": "bg-white", "new_content": "bg-blue-500"}
OBSERVATION: Edit staged. 1 pending change.

THOUGHT: Let me validate the change compiles.
ACTION: validate_changes {}
OBSERVATION: ✓ All imports valid. ✓ No ESLint errors.

THOUGHT: Changes validated. Task complete.
ACTION: finish {"summary": "Changed header background from white to blue"}
```

### Pending Changes System

The agent stages changes before applying:

```python
# Changes are staged, not immediately written
write_file("Button.jsx", content) → adds to pending_changes

# Validation checks pending state
validate_changes() → checks all pending files

# Apply writes all pending changes atomically
apply_changes() → writes files, generates diffs
```

This allows validation before committing changes.

### Advantages

- ✅ **Flexible** — Adapts to any project structure
- ✅ **Exploratory** — Can discover relevant files
- ✅ **Self-correcting** — Validates and retries
- ✅ **Transparent** — Full reasoning trace
- ✅ **Complex tasks** — Handles multi-file changes well

### Disadvantages

- ❌ **More API calls** — 5-50+ iterations
- ❌ **Higher cost** — Each iteration uses tokens
- ❌ **Unpredictable** — Can get stuck in loops
- ❌ **Slower** — 15-120 seconds typical
- ❌ **May hallucinate** — Tools must validate

### When to Use

- Complex, multi-file changes
- Unfamiliar project structure
- Exploratory changes ("find and fix all X")
- When you want reasoning trace
- Research and comparison

---

## Comparison Matrix

### By Task Complexity

| Task | Single-Shot | Multi-Agent | ReAct |
|------|-------------|-------------|-------|
| Change button color | ✅ Fast | ✅ Validated | ⚠️ Overkill |
| Add component prop | ✅ Simple | ✅ Good | ⚠️ Overkill |
| Add new page | ⚠️ Context | ✅ Planned | ✅ Flexible |
| Refactor across files | ❌ Hard | ✅ Planned | ✅ Best |
| "Fix all X patterns" | ❌ No | ⚠️ Retrieval | ✅ Best |

### By Priority

| Priority | Approach |
|----------|----------|
| Speed | Single-Shot |
| Reliability | Multi-Agent |
| Flexibility | ReAct Agent |
| Cost (cheap model) | Single-Shot |
| Cost (best quality) | Multi-Agent |

---

## Future Improvements

### Single-Shot
- [ ] Token limit handling (chunking)
- [ ] Caching for unchanged files

### Multi-Agent
- [ ] Parallel execution of independent steps
- [ ] Better retrieval (hybrid search)
- [ ] Plan refinement loop

### ReAct Agent
- [ ] Tool for running tests
- [ ] Memory across iterations
- [ ] Parallel tool execution
- [ ] Better loop detection
