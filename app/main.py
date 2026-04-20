import os

from fastapi import FastAPI
import asyncio
from fastapi.middleware.cors import CORSMiddleware
from app.services.snapshot_service import init_db, start_snapshot_loop
from app.api.routes.kalshi import router as kalshi_router
import os

app = FastAPI(title="FIFA Mispricing Tracker", version="0.1.0")

app.include_router(kalshi_router, prefix="/kalshi", tags=["kalshi"])



# CORS: allow local dev + production frontend
ALLOWED_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "https://mispricing.smitp.dev",
]

# Optional: allow an extra origin via env var (useful for Vercel preview URLs)
extra_origin = os.getenv("EXTRA_CORS_ORIGIN")
if extra_origin:
    ALLOWED_ORIGINS.append(extra_origin)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
@app.get("/")
def root():
    return {"message": "FIFA Mispricing Tracker is running 🚀"}


@app.get("/health")
def health():
    return {"status": "ok"}

@app.on_event("startup")
async def startup_event():
    init_db()
    asyncio.create_task(start_snapshot_loop()) 
    