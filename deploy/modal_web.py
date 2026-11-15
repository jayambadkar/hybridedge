"""Hosted demo of the HybridEdge web app on Modal (CPU only, cloud models from .env).

Deploy:  uv run modal deploy deploy/modal_web.py
The URL is printed on deploy. State starts fresh on each cold start.
There is no on-device model in the hosted copy, so the offline drill has nothing to fall back to.
"""
from pathlib import Path

import modal

ROOT = Path(__file__).resolve().parents[1]
APP = "/root/app"

image = (
    modal.Image.debian_slim(python_version="3.13")
    .uv_pip_install(
        "aiosqlite>=0.22.1", "fastapi>=0.162.7", "httpx>=0.28.1", "langgraph>=1.2.22",
        "langgraph-checkpoint-sqlite>=3.1.3", "openai>=3.51.0", "python-dotenv>=1.2.4",
    )
    .env({"DATA_DIR": "/tmp/data", "PYTHONPATH": APP})
    .add_local_dir(ROOT / "hybridedge", f"{APP}/hybridedge", ignore=["__pycache__"])
    .add_local_dir(ROOT / "web", f"{APP}/web")
    .add_local_file(ROOT / "bench" / "results.json", f"{APP}/bench/results.json")
)

app = modal.App("hybridedge-web")


@app.function(image=image, secrets=[modal.Secret.from_dotenv(ROOT)], max_containers=1,
              scaledown_window=20 * 60, timeout=60 * 60)
@modal.concurrent(max_inputs=50)
@modal.asgi_app()
def web():
    from hybridedge.server import app as fastapi_app
    return fastapi_app
