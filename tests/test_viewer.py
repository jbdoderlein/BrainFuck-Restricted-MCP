from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from bf_harness.viewer import (
    estimate_api_cost,
    list_sessions,
    parse_trace,
    resolve_model,
    submission_history,
)


class ViewerTests(unittest.TestCase):
    def test_parses_codex_messages_and_completed_tools(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "trace.jsonl"
            records = [
                {"type": "item.completed", "item": {"type": "agent_message", "text": "Working."}},
                {
                    "type": "item.completed",
                    "item": {
                        "type": "mcp_tool_call",
                        "tool": "evaluate_solution",
                        "arguments": {},
                        "result": {"status": "passed"},
                        "status": "completed",
                    },
                },
                {"type": "item.completed", "item": {"type": "agent_message", "text": "Done."}},
            ]
            path.write_text("\n".join(json.dumps(record) for record in records), encoding="utf-8")
            events, result, _metadata = parse_trace(path, "codex")
            self.assertEqual(result, "Done.")
            self.assertEqual([event["kind"] for event in events], ["message", "tool", "message"])

    def test_builds_submission_history_from_write_events(self) -> None:
        events = [
            {
                "kind": "tool",
                "title": "Write Text File",
                "arguments": {"path": "notes.txt", "content": "ignore"},
            },
            {
                "kind": "tool",
                "title": "Write Text File",
                "arguments": {"path": "submission.bf", "content": ",[.]"},
            },
            {
                "kind": "tool",
                "title": "Write Text File",
                "arguments": {"path": "submission.bf", "content": ",[.,]"},
            },
        ]
        artifacts = [{"path": "submission.bf", "content": ",[.,]"}]
        history = submission_history(events, artifacts)
        self.assertEqual([version["content"] for version in history], [",[.]", ",[.,]"])
        self.assertEqual([version["event_number"] for version in history], [2, 3])

    def test_estimates_cost_with_cached_openai_input(self) -> None:
        cost = estimate_api_cost(
            "gpt-5.3-codex",
            {"input_tokens": 1_000_000, "cached_input_tokens": 500_000, "output_tokens": 100_000},
        )
        self.assertTrue(cost["available"])
        self.assertAlmostEqual(cost["amount_usd"], 2.3625)

    def test_recovers_partial_claude_usage_after_budget_stop(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "trace.jsonl"
            records = [
                {"type": "system", "subtype": "init", "model": "claude-sonnet-5"},
                {
                    "type": "stream_event",
                    "event": {
                        "type": "message_start",
                        "message": {
                            "id": "message-1",
                            "usage": {
                                "input_tokens": 2,
                                "cache_read_input_tokens": 10_000,
                                "output_tokens": 3,
                            },
                        },
                    },
                },
                {"type": "system", "subtype": "thinking_tokens", "estimated_tokens": 500},
            ]
            path.write_text("\n".join(json.dumps(record) for record in records), encoding="utf-8")
            _events, _result, metadata = parse_trace(path, "claude")
            self.assertTrue(metadata["usage_is_estimate"])
            self.assertEqual(metadata["usage"]["cache_read_input_tokens"], 10_000)
            self.assertEqual(metadata["usage"]["output_tokens"], 500)

    def test_uses_documented_model_for_default_codex_run(self) -> None:
        model, inferred = resolve_model("codex", {"model": None}, {})
        self.assertEqual(model, "gpt-5.6-sol")
        self.assertTrue(inferred)

    def test_lists_newest_session_first(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name, client in (("20260101-codex-T01-a", "codex"), ("20260102-claude-T01-b", "claude")):
                session = root / name
                (session / "control" / "state").mkdir(parents=True)
                (session / "workspace").mkdir()
                (session / "control" / "launch.json").write_text(
                    json.dumps({"client": client, "problem": "T01"}), encoding="utf-8"
                )
            sessions = list_sessions(root)
            self.assertEqual([session["client"] for session in sessions], ["claude", "codex"])


if __name__ == "__main__":
    unittest.main()
