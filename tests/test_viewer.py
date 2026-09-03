from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from bf_harness.viewer import list_sessions, parse_trace


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
