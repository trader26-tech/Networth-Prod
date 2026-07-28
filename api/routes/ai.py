"""
AI chat route — RAG-powered covered call assistant (Gemma 4 via Ollama).
"""

from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/api/ai", tags=["ai"])


class ChatRequest(BaseModel):
    query: str
    position: Optional[dict] = None
    history: Optional[list] = None      # [{"role": "user"|"assistant", "content": str}]


class ChatResponse(BaseModel):
    answer: str


@router.post("/chat", response_model=ChatResponse)
def chat_endpoint(req: ChatRequest):
    from api.ai.rag import chat  # lazy import so ChromaDB initialises on first use
    answer = chat(req.query, req.position, req.history)
    return ChatResponse(answer=answer)


@router.get("/health")
def ai_health():
    """Check if Ollama is reachable."""
    import httpx
    try:
        r = httpx.get("http://localhost:11434/api/tags", timeout=3.0)
        models = [m["name"] for m in r.json().get("models", [])]
        return {"ollama": "ok", "models": models, "gemma4_ready": any("gemma4" in m for m in models)}
    except Exception:
        return {"ollama": "offline", "models": [], "gemma4_ready": False}
