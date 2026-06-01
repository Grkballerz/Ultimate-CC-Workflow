"""Coverage for the MCP server beyond handle_request:
- tool_pin / tool_forget / tool_list / tool_stats / tool_init via JSON-RPC
- serve_stdio loop with mixed valid/invalid inputs
- main() CLI: --init, --once, garbage input
"""
from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from ucw_memory.server import handle_request, serve_stdio
from ucw_memory.server import main as server_main


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    (tmp_path / ".ucw").mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("UCW_MEMORY_HOME", str(tmp_path / ".claude" / "ucw"))
    return tmp_path


def _call(name, args, req_id=1):
    return handle_request({
        "jsonrpc": "2.0", "id": req_id,
        "method": "tools/call",
        "params": {"name": name, "arguments": args},
    })


def test_memory_init_idempotent(sandbox):
    r1 = _call("memory.init", {"scope": "project"})
    body1 = json.loads(r1["result"]["content"][0]["text"])
    r2 = _call("memory.init", {"scope": "project"})
    body2 = json.loads(r2["result"]["content"][0]["text"])
    assert body1 == body2
    assert Path(body1["db_path"]).exists()


def test_memory_pin_then_list_shows_pinned(sandbox):
    _call("memory.init", {})
    note = _call("memory.note", {"subject": "x", "predicate": "uses",
                                  "object": "y", "reason": "z"})
    fid = json.loads(note["result"]["content"][0]["text"])["id"]
    _call("memory.pin", {"fact_id": fid})
    listing = _call("memory.list", {"scope": "project"})
    facts = json.loads(listing["result"]["content"][0]["text"])["facts"]
    assert any(f["id"] == fid and f["pinned"] for f in facts)


def test_memory_forget_excludes_from_list(sandbox):
    _call("memory.init", {})
    note = _call("memory.note", {"subject": "doomed", "predicate": "is",
                                  "object": "deleted", "reason": "user removed"})
    fid = json.loads(note["result"]["content"][0]["text"])["id"]
    _call("memory.forget", {"fact_id": fid})
    listing = _call("memory.list", {"scope": "project"})
    facts = json.loads(listing["result"]["content"][0]["text"])["facts"]
    assert not any(f["id"] == fid for f in facts)


def test_memory_stats_returns_both_scopes(sandbox):
    _call("memory.init", {})
    _call("memory.init", {"scope": "global"})
    resp = _call("memory.stats", {})
    body = json.loads(resp["result"]["content"][0]["text"])
    assert "project" in body
    assert "global" in body
    assert body["embedding_mode"] in {"fts-only", "fts+rerank-claude", "fts+rerank-voyage"}


def test_memory_list_pagination(sandbox):
    _call("memory.init", {})
    for i in range(5):
        _call("memory.note", {"subject": f"s{i}", "predicate": "is",
                              "object": f"o{i}", "reason": f"r{i}"})
    page1 = _call("memory.list", {"limit": 2, "offset": 0})
    page2 = _call("memory.list", {"limit": 2, "offset": 2})
    p1 = json.loads(page1["result"]["content"][0]["text"])["facts"]
    p2 = json.loads(page2["result"]["content"][0]["text"])["facts"]
    assert len(p1) == 2
    assert len(p2) == 2
    assert {f["id"] for f in p1} & {f["id"] for f in p2} == set()


def test_recall_scope_all_merges(sandbox):
    _call("memory.init", {"scope": "project"})
    _call("memory.init", {"scope": "global"})
    _call("memory.note", {"subject": "p", "predicate": "is", "object": "redis",
                          "reason": "cache", "scope": "project"})
    _call("memory.note", {"subject": "g", "predicate": "is", "object": "vim",
                          "reason": "editor", "scope": "global"})
    resp = _call("memory.recall", {"query": "redis", "scope": "all"})
    body = json.loads(resp["result"]["content"][0]["text"])
    assert any("redis" in h["text"] for h in body["hits"])


# ---- serve_stdio loop --------------------------------------------------------

def test_serve_stdio_handles_garbage_and_valid_input(sandbox, monkeypatch, capsys):
    """Feed serve_stdio a sequence of lines: blank, garbage, valid notification,
    then a valid request. Expect a parse error and one real response."""
    inputs = "\n".join([
        "",                                    # blank → skipped
        "not-json-at-all",                     # garbage → -32700 parse error
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),  # silent
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"}),
    ]) + "\n"
    monkeypatch.setattr("sys.stdin", io.StringIO(inputs))
    rc = serve_stdio()
    out = capsys.readouterr().out
    lines = [json.loads(line) for line in out.strip().splitlines()]
    assert rc == 0
    # First response: parse error
    assert lines[0]["error"]["code"] == -32700
    # Notification produces nothing
    # Second non-notification response: initialize result
    assert lines[1]["result"]["serverInfo"]["name"] == "ucw-memory"


# ---- main() CLI --------------------------------------------------------------

def test_main_init_flag_creates_db(tmp_path, capsys):
    target = tmp_path / "explicit.sqlite"
    rc = server_main(["--init", str(target)])
    assert rc == 0
    assert target.exists()


def test_main_once_with_initialize(sandbox, capsys):
    rc = server_main(["--once", json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"})])
    assert rc == 0
    out = capsys.readouterr().out
    body = json.loads(out)
    assert body["result"]["protocolVersion"]


def test_main_once_with_notification_prints_nothing(sandbox, capsys):
    rc = server_main(["--once", json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"})])
    assert rc == 0
    assert capsys.readouterr().out == ""
