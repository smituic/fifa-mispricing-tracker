import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(__file__)))

import asyncio
from app.services.snapshot_service import start_snapshot_loop, init_db

async def main():
    print("Starting snapshot worker...")
    init_db()
    await start_snapshot_loop()

if __name__ == "__main__":
    asyncio.run(main())