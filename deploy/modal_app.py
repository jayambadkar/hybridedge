"""Modal deployment: Nemotron 3.5 Lightning (30B-A3B) behind vLLM's OpenAI-compatible API.

One-time setup:
    uv run modal token new                       # log in to your Modal workspace
    uv run modal secret create hybridedge-api-key VLLM_API_KEY=$(openssl rand -hex 24)
Deploy:
    uv run modal deploy deploy/modal_app.py
The endpoint is printed on deploy:  https://<workspace>--hybridedge-nemotron-serve.modal.run
Set MODAL_BASE_URL=<that>/v1 and MODAL_API_KEY=<the VLLM_API_KEY above> in .env.

It scales to zero when idle. HybridEdge treats it as a cold standby: it is only
probed (which wakes it) when the primary cloud is unavailable.
"""
import os
import subprocess

import modal

MODEL = "nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-BF16"
SERVED_NAME = "nemotron-lightning"
PORT = 8000

image = modal.Image.from_registry("vllm/vllm-openai:v0.27.1", add_python="3.12").entrypoint([])
hf_cache = modal.Volume.from_name("hybridedge-hf-cache", create_if_missing=True)
vllm_cache = modal.Volume.from_name("hybridedge-vllm-cache", create_if_missing=True)

app = modal.App("hybridedge-nemotron")


@app.function(
    image=image,
    gpu="H100",
    timeout=24 * 60 * 60,
    scaledown_window=10 * 60,
    min_containers=0,
    secrets=[modal.Secret.from_name("hybridedge-api-key")],
    volumes={"/root/.cache/huggingface": hf_cache, "/root/.cache/vllm": vllm_cache},
)
@modal.concurrent(max_inputs=32)
@modal.web_server(port=PORT, startup_timeout=25 * 60)
def serve():
    cmd = [
        "vllm", "serve", MODEL,
        "--served-model-name", SERVED_NAME,
        "--host", "0.0.0.0", "--port", str(PORT),
        "--api-key", os.environ["VLLM_API_KEY"],
        "--max-model-len", "131072",
        "--max-num-seqs", "64",
        "--enable-prefix-caching",
        "--async-scheduling",
        "--mamba-backend", "flashinfer",
        "--mamba-ssm-cache-dtype", "float16",
        "--enable-mamba-cache-stochastic-rounding",
        "--mamba-cache-philox-rounds", "5",
        "--reasoning-parser", "nemotron_v3",
        "--tool-call-parser", "qwen3_coder",
        "--enable-auto-tool-choice",
        "--trust-remote-code",
    ]
    subprocess.Popen(cmd)
