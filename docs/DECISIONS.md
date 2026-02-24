# Technology Decisions

This document explains the key technology choices made in this project and their rationale.

## Web Framework: FastAPI

### Decision
Use **FastAPI** over Flask, Django, or other Python web frameworks.

### Rationale

| Factor | FastAPI | Flask | Django |
|--------|---------|-------|--------|
| **Async support** | Native | Requires extensions | Limited |
| **Type hints** | Required, validated | Optional | Partial |
| **Auto documentation** | OpenAPI/Swagger | Manual | Manual |
| **Performance** | High (Starlette) | Lower | Lower |
| **Learning curve** | Low | Low | Higher |

**Key reasons:**

1. **Native async/await** — LLM API calls are I/O-bound; async allows handling multiple requests efficiently without threads
2. **Pydantic integration** — Request/response validation with zero boilerplate
3. **Auto-generated docs** — `/docs` endpoint shows interactive API documentation
4. **Type safety** — Catches errors at development time, improves IDE support

### Alternatives Considered

- **Flask**: More familiar but lacks native async and type validation
- **Django**: Overkill for API-only service, comes with ORM baggage
- **Express.js (Node)**: Good async support but Python has better LLM tooling

---

## LLM Provider: OpenRouter

### Decision
Use **OpenRouter** as the default LLM provider, with OpenAI API compatibility.

### Rationale

| Factor | OpenRouter | Direct OpenAI | Direct Anthropic |
|--------|------------|---------------|------------------|
| **Model variety** | 100+ models | OpenAI only | Anthropic only |
| **Cost** | Often cheaper | Standard | Standard |
| **API format** | OpenAI-compatible | Native | Different API |
| **Fallback** | Auto-failover | Manual | Manual |

**Key reasons:**

1. **Model flexibility** — Test different models without code changes (just update `.env`)
2. **Cost optimization** — Use cheap models for simple tasks, powerful for code generation
3. **Single API** — OpenAI-compatible format works with `openai` Python SDK
4. **Comparison** — Easy to benchmark Claude vs GPT vs Gemini vs open-source

### Alternatives Considered

- **Direct OpenAI**: Limited to GPT models only
- **Direct Anthropic**: Different API, would need separate client
- **LiteLLM**: Good option but adds another dependency

---

## Vector Search: FAISS

### Decision
Use **FAISS** (Facebook AI Similarity Search) for embedding-based retrieval.

### Rationale

| Factor | FAISS | ChromaDB | Pinecone |
|--------|-------|----------|----------|
| **Setup** | Local, no server | Local or remote | Cloud only |
| **Speed** | Very fast | Good | Network latency |
| **Cost** | Free | Free | Paid |
| **Simplicity** | Simple API | More features | More features |

**Key reasons:**

1. **No external service** — Runs locally, no API keys or network calls for search
2. **Fast** — Optimized C++ with Python bindings
3. **Simple** — Just `index.add()` and `index.search()`, no abstraction layers
4. **Proven** — Used by Meta, reliable and well-documented

### Alternatives Considered

- **ChromaDB**: Good but heavier, more suited for persistent collections
- **Pinecone/Weaviate**: Cloud services add latency and cost for a PoC
- **Pure numpy**: Possible but FAISS is more optimized for large indices

---

## Diff Format: Unified Diff + git apply

### Decision
Generate **unified diff** format and apply via **`git apply`**.

### Rationale

| Factor | Unified Diff | JSON Patch | Line-by-line |
|--------|--------------|------------|--------------|
| **Standard** | Yes (git) | RFC 6902 | Custom |
| **Human readable** | Yes | Somewhat | Yes |
| **Conflict handling** | 3-way merge | Replace only | Manual |
| **Tooling** | Everywhere | Limited | None |

**Key reasons:**

1. **Assignment requirement** — Specification calls for unified diff format
2. **git apply --3way** — Handles whitespace differences and provides merge capability
3. **Familiar format** — Developers understand diffs, easy to review
4. **Rollback** — `git checkout` can revert changes

### Implementation

```python
# Generate diff with difflib
diff = difflib.unified_diff(original.splitlines(), modified.splitlines(), ...)

# Apply with git
subprocess.run(["git", "apply", "--3way", patch_file])
```

---

## Configuration: Pydantic Settings

### Decision
Use **pydantic-settings** for configuration management.

### Rationale

| Factor | Pydantic | python-dotenv | configparser |
|--------|----------|---------------|--------------|
| **Validation** | Type-checked | None | None |
| **Defaults** | In code | Separate file | Separate file |
| **Type coercion** | Automatic | Manual | Manual |
| **IDE support** | Excellent | Limited | Limited |

**Key reasons:**

1. **Single source of truth** — Defaults and types defined in one place (`config.py`)
2. **Validation** — Invalid config fails at startup, not at runtime
3. **Type coercion** — `"true"` in .env becomes `True` in Python automatically
4. **Documentation** — Config class serves as docs for available options

### Example

```python
class Settings(BaseSettings):
    model_executor: str = "anthropic/claude-sonnet-4"
    agent_max_retries: int = 3
    agent_validate_build: bool = True

    class Config:
        env_file = ".env"
```

---

## Output Formats: Three Strategies

### Decision
Support three LLM output formats: **full_content**, **search_replace**, **diff**.

### Rationale

Each format has tradeoffs:

| Format | Tokens | Reliability | Best For |
|--------|--------|-------------|----------|
| `full_content` | High | Highest | Default, guaranteed to work |
| `search_replace` | Medium | High | Surgical edits |
| `diff` | Low | Lower | Cost optimization |

**Key reasons:**

1. **Flexibility** — Different tasks benefit from different formats
2. **Cost control** — `search_replace` and `diff` use fewer tokens
3. **Fallback** — Can switch to `full_content` if others fail
4. **Comparison** — Allows benchmarking format effectiveness

---

## Three Approaches: Why Not Just One?

### Decision
Implement **three distinct approaches** (Single-shot, Multi-agent, ReAct) rather than one optimized solution.

### Rationale

1. **Assignment context** — PoC to demonstrate understanding, not production deployment
2. **Comparison value** — Shows understanding of tradeoffs
3. **Research** — Different approaches excel at different tasks
4. **Learning** — Each implementation reveals different challenges

### Tradeoff Summary

| Approach | Pros | Cons |
|----------|------|------|
| **Single-shot** | Simple, fast | No validation, context limits |
| **Multi-agent** | Validated, cost-optimized | Complex, fixed pipeline |
| **ReAct** | Flexible, self-correcting | Expensive, unpredictable |

A production system might combine elements:
- Use single-shot for simple changes
- Use multi-agent pipeline for validated production changes
- Use ReAct for exploratory or complex refactoring

---

## Build Validation: npm run build

### Decision
Validate generated code by running **`npm run build`** in the target project.

### Rationale

| Validation | What it catches | Speed |
|------------|-----------------|-------|
| ESLint | Syntax, style | Fast |
| TypeScript | Type errors | Medium |
| npm build | All of above + bundling | Slower |

**Key reasons:**

1. **Comprehensive** — Catches syntax, imports, and bundling issues
2. **Real-world** — Uses the project's actual build config
3. **Assignment alignment** — Sample projects use Vite which has fast builds
4. **Retry opportunity** — Build errors feed back to LLM for correction

### Tradeoffs Accepted

- Slower than linting alone
- Requires `npm install` in sample projects
- May fail for reasons unrelated to generated code

---

## Error Handling: Retry with Feedback

### Decision
On validation failure, **retry with error context** rather than failing immediately.

### Rationale

LLMs can often fix their mistakes when given:
1. The error message
2. The code that caused it
3. A clear instruction to fix

```python
# Feedback format
f"""
Your previous attempt failed with:
{error_message}

The file that caused this error:
{file_content}

Please fix this issue and regenerate.
"""
```

### Configuration

```env
AGENT_MAX_RETRIES=3  # Default: 3 attempts before failing
```

---

## What I Would Do Differently in Production

| Area | PoC Implementation | Production Improvement |
|------|-------------------|----------------------|
| **Auth** | None | API key authentication |
| **Persistence** | File-based | Database for history |
| **Scaling** | Single process | Multiple workers, queue |
| **Caching** | Basic FAISS cache | Redis for embeddings |
| **Monitoring** | Logging only | Metrics, APM |
| **Testing** | Manual | Unit + integration tests |
| **Security** | Trusted input | Sandbox execution |
