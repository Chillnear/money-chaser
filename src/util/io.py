"""
I/O ล้วนๆ ที่ใช้ร่วมกันหลายที่ (main.py, execution/reconcile.py ทำแบบเดียวกันแยกไฟล์ไว้แล้วสำหรับ run-lock
โดยเฉพาะ — ในนี้คือ generic JSON/JSONL helper สำหรับ journal state) ไม่มี logic ตัดสินใจใดๆ ในไฟล์นี้
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def load_jsonl(path: Path) -> list[dict]:
    """อ่านไฟล์ append-only JSONL (1 JSON object ต่อบรรทัด) — คืน [] ถ้าไฟล์ไม่มีหรือมีบรรทัดเสีย
    ข้ามบรรทัดที่ parse ไม่ผ่านแทนที่จะทำทั้งไฟล์ล้ม (best-effort เหมือน load_json)
    """
    if not path.exists():
        return []
    records: list[dict] = []
    try:
        raw_lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in raw_lines:
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records
