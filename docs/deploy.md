# Deployment

HybridEdge needs one or more OpenAI-compatible endpoints serving Nemotron 3.5 Lightning, and
Ollama on the device. Providers are listed in `.env` in failover order (`PROVIDERS=verda,modal`);
each has `<NAME>_BASE_URL`, `<NAME>_API_KEY`, `<NAME>_MODEL`, and optionally `<NAME>_STANDBY`
and `<NAME>_CONCURRENCY`.

## vLLM settings

Both clouds run `vllm/vllm-openai:v0.27.1` with
`nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-BF16` on one H100:

```
--served-model-name nemotron-lightning
--max-model-len 131072 --max-num-seqs 64
--enable-prefix-caching --async-scheduling
--mamba-backend flashinfer --mamba-ssm-cache-dtype float16
--reasoning-parser nemotron_v3
--tool-call-parser qwen3_coder --enable-auto-tool-choice
--trust-remote-code
```

## Verda

Create a serverless container with the image and arguments above, port 8000, on 1× H100
SXM5, with `VLLM_API_KEY` set. Put the container URL (ending in `/v1`) and the key in `.env`.
It bills while running (about $5.77/h at the time of writing), so pause it when idle.

## Modal

```bash
uv run modal deploy deploy/modal_app.py
```

The endpoint is printed on deploy. It scales to zero after 10 idle minutes; with
`MODAL_STANDBY=true`, HybridEdge only wakes it when Verda is down or full.

## On-device model

```bash
ollama pull nemotron-3.5-bolt:e4b
ollama create hybridedge-bolt -f Modelfile     # 16k context
```

`./start.sh` does both on first run. The server keeps the model loaded, so failover has no
cold start.
