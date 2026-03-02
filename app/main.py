from fastapi import FastAPI
import asyncio
from app.services.snapshot_service import init_db, start_snapshot_loop
from app.api.routes.kalshi import router as kalshi_router

app = FastAPI(title="FIFA Mispricing Tracker", version="0.1.0")

app.include_router(kalshi_router, prefix="/kalshi", tags=["kalshi"])


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