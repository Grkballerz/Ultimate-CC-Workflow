"""End-to-end JSON-RPC tests for the memory MCP server.

We call `handle_request` directly to avoid spawning subprocesses, and we
sandbox the DB by setting cwd to a tmp dir that already has a `.ucw/` skeleton.
"""
from __future__ import annotations

import json

import pytest
from ucw_memory.server import handle_request


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    (tmp_path / ".ucw").mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("UCW_MEMORY_HOME", str(tmp_path / ".claude" / "ucw"))
    return tmp_path


def _call(name, arguments, req_id=1):
    return handle_request({
        "jsonrpc": "2.0",
        "id": req_id,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    })


def test_initialize_and_tools_list(sandbox):
    init = handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert init["result"]["protocolVersion"]
    assert init["result"]["serverInfo"]["name"] == "ucw-memory"

    tools = handle_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    names = {t["name"] for t in tools["result"]["tools"]}
    for required in {
        "memory.recall", "memory.note", "memory.pin", "memory.forget",
        "memory.list", "memory.stats", "memory.init",
    }:
        assert required in names


def test_note_then_recall_via_jsonrpc(sandbox):
    init_resp = _call("memory.init", {"scope": "project"})
    assert "error" not in init_resp

    note_resp = _call("memory.note", {
        "subject": "demo-app", "predicate": "uses", "object": "flask",
        "reason": "quick prototype framework",
    }, req_id=2)
    note_body = json.loads(note_resp["result"]["content"][0]["text"])
    assert note_body["id"] >= 1

    recall_resp = _call("memory.recall", {"query": "flask"}, req_id=3)
    recall_body = json.loads(recall_resp["result"]["content"][0]["text"])
    assert recall_body["embedding_mode"] == "fts-only"
    assert any("flask" in h["text"] for h in recall_body["hits"])


def test_note_rejects_missing_reason(sandbox):
    resp = _call("memory.note", {
        "subject": "x", "predicate": "y", "object": "z", "reason": "  ",
    })
    assert "error" in resp
    assert "reason required" in resp["error"]["message"]


def test_unknown_tool(sandbox):
    resp = _call("memory.does_not_exist", {})
    assert "error" in resp
    assert resp["error"]["code"] == -32601


def test_initialize_notification_returns_none(sandbox):
    resp = handle_request({"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert resp is None


def test_missing_required_args_returns_clean_error(sandbox):
    """Schema-validation path: missing required fields → -32602 with field list."""
    resp = _call("memory.note", {})
    assert "error" in resp
    assert resp["error"]["code"] == -32602
    assert "missing required argument" in resp["error"]["message"]
    for field in ("subject", "predicate", "object", "reason"):
        assert field in resp["error"]["message"]
    # data carries machine-readable details
    assert resp["error"]["data"]["tool"] == "memory.note"
    assert set(resp["error"]["data"]["required"]) >= {"subject", "predicate", "object", "reason"}


def test_partial_required_args(sandbox):
    """Only some required fields missing."""
    resp = _call("memory.note", {"subject": "x", "predicate": "y"})
    assert resp["error"]["code"] == -32602
    msg = resp["error"]["message"]
    assert "object" in msg
    assert "reason" in msg


def test_unknown_method_returns_method_not_found(sandbox):
    resp = handle_request({"jsonrpc": "2.0", "id": 99, "method": "foo/bar"})
    assert resp["error"]["code"] == -32601


def test_parse_error_invalid_json_via_handle_request_not_applicable():
    """parse errors are returned by serve_stdio; this confirms handle_request
    only receives parsed dicts so it never sees raw garbage."""
    # If someone passes a non-dict, that's a programmer error; we want a stable failure.
    import pytest
    with pytest.raises(AttributeError):
        handle_request("not a dict")  # type: ignore[arg-type]
