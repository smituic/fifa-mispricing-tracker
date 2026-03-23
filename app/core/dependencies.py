from app.services.kalshi_client import KalshiClient
from app.core.config import settings

def get_kalshi_client() -> KalshiClient:
    return KalshiClient(base_url=settings.KALSHI_BASE_URL)