#!/usr/bin/env bash
# Start HybridEdge: local Nemotron (Ollama) + the edge server/web app on http://localhost:8787
set -euo pipefail
cd "$(dirname "$0")"

if ! curl -sf http://127.0.0.1:11434/api/tags >/dev/null; then
  echo "Starting Ollama…"
  (ollama serve >/tmp/hybridedge-ollama.log 2>&1 &)
  for _ in $(seq 1 30); do curl -sf http://127.0.0.1:11434/api/tags >/dev/null && break; sleep 1; done
fi

if ! ollama list | grep -q '^hybridedge-bolt'; then
  echo "Preparing on-device model (Nemotron 3.5 Bolt E4B, 16k context)…"
  ollama pull nemotron-3.5-bolt:e4b
  ollama create hybridedge-bolt -f Modelfile
fi

# Warm the local model so the first failover is instant.
curl -s http://127.0.0.1:11434/api/generate -d '{"model":"hybridedge-bolt","prompt":"","keep_alive":"5m"}' >/dev/null || true

[ -f .env ] || cp .env.example .env
echo "HybridEdge → http://localhost:${PORT:-8787}"
exec uv run uvicorn hybridedge.server:app --host 127.0.0.1 --port "${PORT:-8787}"
