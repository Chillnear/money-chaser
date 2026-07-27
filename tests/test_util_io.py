from __future__ import annotations

from src.util.io import append_jsonl, load_json, load_jsonl, save_json


def test_save_and_load_json_roundtrip(tmp_path):
    path = tmp_path / "state.json"
    save_json(path, {"a": 1, "b": "ค่า"})
    assert load_json(path) == {"a": 1, "b": "ค่า"}


def test_load_json_returns_default_when_missing(tmp_path):
    assert load_json(tmp_path / "missing.json", default={"x": 1}) == {"x": 1}


def test_load_json_returns_default_on_corrupt_file(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("not json{{{", encoding="utf-8")
    assert load_json(path, default=[]) == []


def test_append_jsonl_creates_one_line_per_call(tmp_path):
    path = tmp_path / "log.jsonl"
    append_jsonl(path, {"n": 1})
    append_jsonl(path, {"n": 2})
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2


def test_load_jsonl_reads_all_records_in_order(tmp_path):
    path = tmp_path / "log.jsonl"
    append_jsonl(path, {"n": 1})
    append_jsonl(path, {"n": 2})
    append_jsonl(path, {"n": 3})

    records = load_jsonl(path)

    assert [r["n"] for r in records] == [1, 2, 3]


def test_load_jsonl_missing_file_returns_empty_list(tmp_path):
    assert load_jsonl(tmp_path / "missing.jsonl") == []


def test_load_jsonl_skips_corrupt_lines_but_keeps_good_ones(tmp_path):
    path = tmp_path / "log.jsonl"
    path.write_text('{"n": 1}\nnot json\n{"n": 2}\n', encoding="utf-8")

    records = load_jsonl(path)

    assert [r["n"] for r in records] == [1, 2]


def test_load_jsonl_skips_blank_lines(tmp_path):
    path = tmp_path / "log.jsonl"
    path.write_text('{"n": 1}\n\n\n{"n": 2}\n', encoding="utf-8")

    records = load_jsonl(path)

    assert [r["n"] for r in records] == [1, 2]
