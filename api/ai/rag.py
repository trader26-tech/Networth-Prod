"""
RAG pipeline: ChromaDB vector store + Ollama Gemma 4 generation.

Setup (one-time):
  brew install ollama
  ollama serve          # start in background
  ollama pull gemma4    # pull the model (~5GB)
  pip install chromadb httpx
"""

import httpx
import chromadb
from chromadb.utils import embedding_functions

from api.ai.knowledge import CHUNKS

OLLAMA_BASE    = "http://localhost:11434"
OLLAMA_MODEL   = "gemma4"
COLLECTION_NAME = "covered_call_kb"

_collection = None


def _init_collection():
    global _collection
    if _collection is not None:
        return

    client = chromadb.Client()  # in-memory; fast, no disk needed for this KB size
    ef = embedding_functions.DefaultEmbeddingFunction()  # uses all-MiniLM-L6-v2

    _collection = client.get_or_create_collection(COLLECTION_NAME, embedding_function=ef)

    existing_ids = _collection.get()["ids"]
    new_chunks = [c for c in CHUNKS if c["id"] not in existing_ids]
    if new_chunks:
        _collection.add(
            ids=[c["id"] for c in new_chunks],
            documents=[c["text"] for c in new_chunks],
            metadatas=[{"topic": c["topic"]} for c in new_chunks],
        )


def retrieve(query: str, n: int = 4) -> list[str]:
    _init_collection()
    results = _collection.query(query_texts=[query], n_results=min(n, len(CHUNKS)))
    return results["documents"][0]


def _build_position_context(pos: dict) -> str:
    if not pos:
        return ""
    lines = ["Current position:"]
    mapping = {
        "spot":           ("Nifty spot",        lambda v: f"₹{v:,.0f}"),
        "strike":         ("Strike (K)",         lambda v: f"₹{v:,.0f}"),
        "premium":        ("Premium collected",  lambda v: f"₹{v:.2f}/unit"),
        "premium_total":  ("Total premium",      lambda v: f"₹{v:,.0f}"),
        "dte":            ("Days to expiry",     lambda v: f"{v}d"),
        "iv":             ("Implied volatility", lambda v: f"{v:.1f}%"),
        "shares":         ("NiftyBees shares",   lambda v: f"{v:,.0f}"),
        "niftybees_price":("NiftyBees entry",    lambda v: f"₹{v:.2f}"),
        "niftybees_cost": ("NiftyBees cost",     lambda v: f"₹{v:,.0f}"),
        "lots":           ("Lots",               lambda v: f"{v}"),
        "lot_size":       ("Lot size",           lambda v: f"{v} units"),
        "breakeven":      ("Breakeven",          lambda v: f"₹{v:,.0f}"),
        "max_profit":     ("Max profit",         lambda v: f"₹{v:,.0f}"),
        "coverage_ratio": ("Coverage ratio",     lambda v: f"{v}%"),
    }
    for key, (label, fmt) in mapping.items():
        if key in pos and pos[key] is not None:
            try:
                lines.append(f"  {label}: {fmt(pos[key])}")
            except Exception:
                lines.append(f"  {label}: {pos[key]}")
    return "\n".join(lines)


def chat(query: str, position: dict | None = None, history: list[dict] | None = None) -> str:
    docs = retrieve(query)
    knowledge_block = "\n\n".join(f"• {d}" for d in docs)
    position_block  = _build_position_context(position or {})

    system_content = (
        "You are a covered call strategy assistant for Indian markets (Nifty 50 index options + NiftyBees ETF). "
        "Give concise, specific, actionable advice. Use ₹ for amounts. "
        "Refer to the position numbers below when answering.\n\n"
        f"=== Relevant Knowledge ===\n{knowledge_block}\n\n"
        + (f"=== {position_block} ===\n\n" if position_block else "")
        + "Answer in 3-5 sentences. Be direct and use the actual numbers from the position."
    )

    messages = [{"role": "system", "content": system_content}]

    if history:
        for msg in history[-6:]:  # last 3 exchanges
            messages.append({"role": msg["role"], "content": msg["content"]})

    messages.append({"role": "user", "content": query})

    try:
        resp = httpx.post(
            f"{OLLAMA_BASE}/api/chat",
            json={
                "model": OLLAMA_MODEL,
                "messages": messages,
                "stream": False,
                "options": {"temperature": 0.25, "num_predict": 400},
            },
            timeout=90.0,
        )
        resp.raise_for_status()
        return resp.json()["message"]["content"].strip()

    except httpx.ConnectError:
        return (
            "Ollama is not running. To set it up:\n"
            "1. `brew install ollama`\n"
            "2. `ollama serve` (keep running in background)\n"
            "3. `ollama pull gemma4`\n"
            "Then retry your question."
        )
    except httpx.HTTPStatusError as e:
        if "model" in str(e.response.text).lower() and "not found" in str(e.response.text).lower():
            return "Model 'gemma4' not pulled yet. Run: `ollama pull gemma4`"
        return f"Ollama error: {e.response.text}"
    except Exception as e:
        return f"Error: {e}"
