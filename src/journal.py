"""Append-only JSONL decision log. Every scan writes one record with the full
reasoning — score, signals, band, gate verdict, risk verdict, and any order —
so the agent's behaviour is completely auditable after the fact. This record is
the point of the project: the honest trail, not a P&L curve.
"""
from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


class Journal:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, record: dict) -> None:
        record = dict(record)
        record.setdefault("ts", datetime.now(timezone.utc).isoformat())
        record.setdefault("ts_et", datetime.now(ET).isoformat())
        with open(self.path, "a") as f:
            f.write(json.dumps(record, default=str) + "\n")

    def console(self, record: dict) -> None:
        """One-line human summary for the live demo."""
        t = record.get("type", "?")
        sym = record.get("symbol", "")
        if t == "decision":
            g = record.get("gate", {})
            msg = f"{sym:5} score={record.get('score')} -> {'GO' if g.get('go') else 'no-go'} [{g.get('source')}] {g.get('rationale')}"
        elif t == "entry":
            msg = f"{sym:5} ENTRY {record.get('direction')} {record.get('contract')} @ ${record.get('price')} x{record.get('qty')} ({record.get('mode')})"
        elif t == "exit":
            msg = f"{sym:5} EXIT  {record.get('contract')} @ ${record.get('price')} reason={record.get('reason')} pnl=${record.get('pnl')}"
        else:
            msg = json.dumps(record, default=str)
        print(f"[{record.get('ts_et','')[11:19]}] {msg}")
