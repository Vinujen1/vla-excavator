"""Run the Pi service: `python -m excavator_pi` (configure with PI_* environment variables)."""
import logging
import os

import uvicorn

from .app import create_app

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

if __name__ == "__main__":
    uvicorn.run(create_app(), host=os.environ.get("PI_HOST", "127.0.0.1"), port=int(os.environ.get("PI_PORT", "8100")))
