import json

import pytest

from expsched.engine.journal import Journal


def sample() -> Journal:
    j = Journal()
    j.append("READY", 0, task="prep_data")
    j.append("START", 0, task="prep_data", worker="node-C", attempt=1, reason={"code": "TEST"})
    j.append("FINISH", 2, task="prep_data", attempt=1, worker="node-C")
    return j


def test_records_get_monotonic_seq_and_required_fields():
    records = sample().records
    assert [r["seq"] for r in records] == [0, 1, 2]
    assert all({"seq", "t", "type"} <= r.keys() for r in records)
    assert records[1] == {
        "seq": 1,
        "t": 0,
        "type": "START",
        "task": "prep_data",
        "worker": "node-C",
        "attempt": 1,
        "reason": {"code": "TEST"},
    }


def test_append_returns_the_stored_record():
    j = Journal()
    record = j.append("READY", 3, task="a")
    assert record is j.records[0] and len(j) == 1


def test_seq_cannot_be_overridden():
    with pytest.raises(ValueError, match="seq"):
        Journal().append("READY", 0, seq=42)


def test_jsonl_roundtrip_with_lf_and_utf8(tmp_path):
    j = sample()
    j.append("DIAGNOSTIC", 2, code="STUCK", detail="задача ждёт")
    path = tmp_path / "events.jsonl"
    j.write_jsonl(path)
    raw = path.read_bytes()
    assert b"\r" not in raw and raw.endswith(b"\n")
    assert [json.loads(line) for line in raw.decode("utf-8").splitlines()] == list(j.records)


def test_same_journal_gives_same_hash_and_any_change_alters_it():
    assert sample().result_hash() == sample().result_hash()
    changed = sample()
    changed.append("FINISH", 3, task="x", attempt=1, worker="node-C")
    assert changed.result_hash() != sample().result_hash()


def test_empty_journal():
    j = Journal()
    assert len(j) == 0 and j.records == [] and j.result_hash().startswith("sha256:")
