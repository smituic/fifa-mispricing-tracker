from fastapi import FastAPI
from app.api.routes.kalshi import router as kalshi_router

app = FastAPI(title="FIFA Mispricing Tracker", version="0.1.0")

app.include_router(kalshi_router, prefix="/kalshi", tags=["kalshi"])


@app.get("/")
def root():
    return {"message": "FIFA Mispricing Tracker is running 🚀"}


@app.get("/health")
def health():
    return {"status": "ok"}
