import logging
import sys
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.api.agent_routes import router as agent_router
from app.api.react_routes import router as react_router
from app.config import get_settings

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)

# Set DEBUG level for our app modules
logging.getLogger("app").setLevel(logging.DEBUG)

logger = logging.getLogger(__name__)
settings = get_settings()

app = FastAPI(
    title="AI Code Editor API",
    description="API for generating code changes from natural language instructions",
    version="0.1.0"
)

# CORS middleware for future frontend integration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routes
app.include_router(router, prefix="/api/v1", tags=["code-generation"])
app.include_router(agent_router, prefix="/api/v1/agent", tags=["agent"])
app.include_router(react_router, prefix="/api/v1/react", tags=["react-agent"])


@app.on_event("startup")
async def startup_event():
    logger.info(f"Starting AI Code Editor API")
    logger.info(f"LLM Base URL: {settings.llm_base_url}")
    logger.info(f"Models - Simple: {settings.model_simple}, Executor: {settings.model_executor}, ReAct: {settings.model_react}")
    logger.info(f"Projects path: {settings.projects_base_path}")


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "models": {
            "simple": settings.model_simple,
            "intent": settings.model_intent,
            "planner": settings.model_planner,
            "executor": settings.model_executor,
            "react": settings.model_react
        }
    }
