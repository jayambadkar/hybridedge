"""Runtime configuration, loaded from environment / .env."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _f(name: str, default: float) -> float:
    return float(os.getenv(name, default))


@dataclass(frozen=True)
class Provider:
    """An OpenAI-compatible cloud endpoint serving Nemotron (vLLM on Verda, Modal, ...)."""

    name: str
    label: str
    base_url: str
    api_key: str
    model: str
    hardware: str
    standby: bool = False  # cold standby: only probed (and thereby woken) when higher-priority clouds are down/saturated
    capacity: int = 32  # concurrent generations this endpoint should take before work overflows to the next provider

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.api_key)


def _providers() -> list[Provider]:
    """Cloud providers in failover priority order (PROVIDERS=verda,modal)."""
    out = []
    for name in [p.strip() for p in os.getenv("PROVIDERS", "verda,modal").split(",") if p.strip()]:
        up = name.upper()
        out.append(
            Provider(
                name=name,
                label=os.getenv(f"{up}_LABEL", name.capitalize()),
                base_url=os.getenv(f"{up}_BASE_URL", "").rstrip("/"),
                api_key=os.getenv(f"{up}_API_KEY", ""),
                model=os.getenv(f"{up}_MODEL", "nemotron-lightning"),
                hardware=os.getenv(f"{up}_HARDWARE", "H100"),
                standby=os.getenv(f"{up}_STANDBY", "false").lower() == "true",
                capacity=int(os.getenv(f"{up}_CONCURRENCY", "32")),
            )
        )
    return out


@dataclass(frozen=True)
class Settings:
    providers: list[Provider] = field(default_factory=_providers)
    cloud_model_label: str = os.getenv("CLOUD_MODEL_LABEL", "Nemotron 3.5 Lightning 30B")
    cloud_thinking: bool = os.getenv("CLOUD_THINKING", "false").lower() == "true"

    # Edge tier: Nemotron 3.5 Bolt E4B running locally in Ollama.
    edge_base_url: str = os.getenv("EDGE_BASE_URL", "http://127.0.0.1:11434/v1")
    edge_model: str = os.getenv("EDGE_MODEL", "hybridedge-bolt")
    # How long Ollama keeps the device model in memory after a call. "-1" pins it (instant failover, ~15 GB held).
    edge_keep_alive: str = os.getenv("EDGE_KEEP_ALIVE", "10m")
    edge_label: str = os.getenv("EDGE_LABEL", "Nemotron 3.5 Bolt E4B")
    edge_context_chars: int = int(os.getenv("EDGE_CONTEXT_CHARS", "36000"))
    edge_concurrency: int = int(os.getenv("EDGE_CONCURRENCY", "1"))

    # Multi-agent scaling: how many specialists a plan may fan out to on each tier.
    cloud_max_agents: int = int(os.getenv("CLOUD_MAX_AGENTS", "5"))
    edge_max_agents: int = int(os.getenv("EDGE_MAX_AGENTS", "2"))

    # Link monitoring / failover
    health_interval_s: float = _f("HEALTH_INTERVAL_S", 2.0)
    health_timeout_s: float = _f("HEALTH_TIMEOUT_S", 3.0)
    fail_threshold: int = int(os.getenv("FAIL_THRESHOLD", "2"))
    first_token_timeout_s: float = _f("FIRST_TOKEN_TIMEOUT_S", 30.0)
    stall_timeout_s: float = _f("STALL_TIMEOUT_S", 20.0)
    # The device queues parallel specialists and may need ~15 s to load the model, so it gets longer.
    edge_first_token_timeout_s: float = _f("EDGE_FIRST_TOKEN_TIMEOUT_S", 180.0)

    data_dir: Path = Path(os.getenv("DATA_DIR", str(ROOT / "data")))
    max_agent_steps: int = int(os.getenv("MAX_AGENT_STEPS", "6"))


settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
