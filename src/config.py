"""Load config.json + secrets from the environment.

Keys never live in the repo. They come from environment variables (or a local
.env that .gitignore excludes). config.json holds only non-secret behaviour.
"""
from __future__ import annotations
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader (no dependency). Does not overwrite real env vars."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


@dataclass
class Secrets:
    alpaca_key: str | None
    alpaca_secret: str | None
    alpaca_paper: bool
    anthropic_key: str | None

    @property
    def have_alpaca(self) -> bool:
        return bool(self.alpaca_key and self.alpaca_secret)


@dataclass
class Config:
    raw: dict[str, Any]
    secrets: Secrets = field(repr=False)

    # convenience accessors -------------------------------------------------
    @property
    def mode(self) -> str:
        return self.raw.get("mode", "dry_run")

    @property
    def decision_mode(self) -> str:
        return self.raw.get("decision_mode", "rules_only")

    @property
    def symbols(self) -> list[str]:
        return list(self.raw.get("symbols", ["SPY", "QQQ", "IWM"]))

    @property
    def score(self) -> dict:
        return self.raw.get("score", {})

    @property
    def exits(self) -> dict:
        return self.raw.get("exits", {})

    @property
    def risk(self) -> dict:
        return self.raw.get("risk", {})

    @property
    def entry_rules(self) -> dict:
        return self.raw.get("entry_rules", {})

    @property
    def regime(self) -> dict:
        return self.raw.get("regime", {})

    @property
    def scan(self) -> dict:
        return self.raw.get("scan", {})

    @property
    def llm(self) -> dict:
        return self.raw.get("llm", {})

    @property
    def gamma(self) -> dict:
        return self.raw.get("gamma", {})


def load_config(path: str | Path | None = None) -> Config:
    _load_dotenv(ROOT / ".env")
    cfg_path = Path(path) if path else ROOT / "config.json"
    raw = json.loads(cfg_path.read_text())
    paper = os.environ.get("ALPACA_PAPER", "true").strip().lower() not in ("false", "0", "no")
    secrets = Secrets(
        alpaca_key=os.environ.get("ALPACA_API_KEY"),
        alpaca_secret=os.environ.get("ALPACA_SECRET_KEY"),
        alpaca_paper=paper,
        anthropic_key=os.environ.get("ANTHROPIC_API_KEY"),
    )
    return Config(raw=raw, secrets=secrets)
