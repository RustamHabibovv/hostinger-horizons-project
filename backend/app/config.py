from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # LLM Provider settings (OpenRouter or OpenAI compatible)
    # For OpenRouter: https://openrouter.ai/api/v1
    # For OpenAI: https://api.openai.com/v1
    llm_api_key: str = ""
    llm_base_url: str = "https://api.openai.com/v1"
    
    # Model selection per task - allows cost optimization
    # For OpenRouter use: provider/model (e.g., anthropic/claude-sonnet-4, google/gemini-2.5-flash)
    # For OpenAI use: model name (e.g., gpt-4o, o1-mini)
    
    # Simple endpoint model
    model_simple: str = "google/gemini-3.1-pro-preview"  # For /generate endpoint
    
    # Agent step models (allows mixing cheap/powerful models)
    model_intent: str = "anthropic/claude-sonnet-4.5"   # Intent parsing (cheap, fast)
    model_planner: str = "anthropic/claude-sonnet-4.5"  # Execution planning (cheap, fast)  
    model_executor: str = "google/gemini-3.1-pro-preview"      # Code generation (best quality)
    model_embedding: str = "text-embedding-3-small"  # Vector embeddings
    
    # Generation settings
    llm_temperature: float = 0.2
    max_tokens: int = 8192
    
    # Path to the sample React projects
    projects_base_path: str = "../sample-react-projects"
    
    # Simple endpoint settings
    output_format: str = "full_content"  # full_content, search_replace, or diff
    
    # Agent settings
    agent_max_retries: int = 3
    agent_retrieval_top_k: int = 5
    agent_validate_build: bool = True
    agent_validation_timeout: int = 60
    agent_verbose: bool = False
    agent_output_format: str = "full_content"  # full_content, search_replace, or diff
    
    # ReAct agent settings
    react_max_iterations: int = 15
    model_react: str = "google/gemini-3.1-pro-preview"  # ReAct agent model
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache
def get_settings() -> Settings:
    return Settings()
