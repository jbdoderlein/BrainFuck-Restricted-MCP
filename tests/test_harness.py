from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
import tomllib
import unittest
import zipfile
from pathlib import Path

from bf_harness.evaluator import BrainfuckEvaluator, HarnessError
from bf_harness.launch import (
    CODEX_MODELS,
    codex_selection_arguments,
    export_session,
    finalize_state,
    run_client,
    write_codex_config,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def program_for_text(text: str) -> str:
    value = 0
    program: list[str] = []
    for byte in text.encode("utf-8"):
        increase = (byte - value) % 256
        decrease = (value - byte) % 256
        if increase <= decrease:
            program.append("+" * increase)
        else:
            program.append("-" * decrease)
        program.append(".")
        value = byte
    return "".join(program)


class HarnessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.artifacts = self.root / "artifacts"
        self.state = self.root / "state"
        self.tests_root = self.root / "tests"
        (self.tests_root / "semi_private").mkdir(parents=True)
        (self.tests_root / "private").mkdir(parents=True)
        self.problem = {
            "id": "T01",
            "runtime": {
                "timeout_seconds": 1.0,
                "memory_bytes": 536870912,
                "max_output_bytes": 4096,
                "max_source_bytes": 65536,
                "max_text_file_bytes": 65536,
                "max_artifact_bytes": 131072,
                "max_input_bytes": 65536,
                "max_artifact_files": 8,
            },
            "input_output_examples": [{"input": "", "output": "public"}],
        }
        self._write_suite("semi_private", [{"input": "", "output": "semi"}])
        self._write_suite(
            "private",
            [{"input": "PRIVATE_INPUT_MARKER", "output": "PRIVATE_EXPECTED_MARKER"}],
        )
        self.evaluator = BrainfuckEvaluator(
            problem=self.problem,
            tests_root=self.tests_root,
            artifact_root=self.artifacts,
            state_root=self.state,
            interpreter=PROJECT_ROOT / "tritium",
            bubblewrap=Path("/usr/bin/bwrap"),
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _write_suite(self, name: str, tests: list[dict[str, str]]) -> None:
        path = self.tests_root / name / "T01.json"
        path.write_text(
            json.dumps({"problem_id": "T01", "tests": tests}), encoding="utf-8"
        )

    def test_runs_fixed_interpreter(self) -> None:
        self.evaluator.write_text_file("submission.bf", program_for_text("Hello"))
        result = self.evaluator.run("")
        self.assertEqual(result, {"status": "completed", "output": "Hello"})

    def test_rejects_path_traversal(self) -> None:
        with self.assertRaises(HarnessError):
            self.evaluator.write_text_file("../escape.txt", "text")

    def test_writes_arbitrary_text_without_execution(self) -> None:
        content = "print('This text must not execute.')\n"
        result = self.evaluator.write_text_file("notes/example.py", content)
        self.assertEqual(result["status"], "written")
        self.assertEqual((self.artifacts / "notes" / "example.py").read_text(), content)

    def test_output_limit_stops_infinite_output(self) -> None:
        self.evaluator.write_text_file("submission.bf", "+[.]")
        result = self.evaluator.run("")
        self.assertEqual(result["status"], "output_limit")

    def test_semi_private_failure_reveals_first_test(self) -> None:
        self.evaluator.write_text_file("submission.bf", program_for_text("public"))
        result = self.evaluator.evaluate()
        self.assertEqual(result["test_set"], "semi_private")
        self.assertEqual(result["expected"], "semi")

    def test_private_failures_are_generic_until_attempt_limit(self) -> None:
        self.evaluator.public_tests = [{"input": "public", "output": "public"}]
        self.evaluator.semi_private_tests = [{"input": "semi", "output": "semi"}]
        self.evaluator.write_text_file("submission.bf", ",[.,]")
        first = self.evaluator.submit()
        serialized = json.dumps(first)
        self.assertEqual(
            first,
            {
                "status": "failed",
                "test_set": "private",
                "message": "A hidden test failed.",
                "final_attempts_used": 1,
                "final_attempts_remaining": 2,
                "session_ended": False,
            },
        )
        self.assertNotIn("PRIVATE_INPUT_MARKER", serialized)
        self.assertNotIn("PRIVATE_EXPECTED_MARKER", serialized)
        self.evaluator.write_text_file("notes.md", "The session is still active.")
        self.evaluator.submit()
        last = self.evaluator.submit()
        self.assertEqual(last["final_attempts_remaining"], 0)
        self.assertTrue(last["session_ended"])
        audit = self.evaluator.audit_path.read_text(encoding="utf-8")
        self.assertNotIn("PRIVATE_INPUT_MARKER", audit)
        self.assertNotIn("PRIVATE_EXPECTED_MARKER", audit)
        with self.assertRaises(HarnessError):
            self.evaluator.write_text_file("locked.md", "locked")

    def test_final_submission_can_pass_all_test_sets(self) -> None:
        self.evaluator.public_tests = [{"input": "public", "output": "public"}]
        self.evaluator.semi_private_tests = [{"input": "semi", "output": "semi"}]
        self.evaluator.private_tests = [{"input": "private", "output": "private"}]
        self.evaluator.write_text_file("submission.bf", ",[.,]")
        self.assertEqual(
            self.evaluator.submit(),
            {
                "status": "passed",
                "message": "All tests passed.",
                "final_attempts_used": 1,
                "final_attempts_remaining": 2,
                "session_ended": True,
            },
        )

    def test_invalid_final_calls_end_at_attempt_limit(self) -> None:
        for _ in range(3):
            with self.assertRaises(HarnessError):
                self.evaluator.submit()
        status = self.evaluator.budget_status()
        self.assertEqual(status["status"], "ended")
        self.assertEqual(status["end_reason"], "attempt_limit")
        self.assertEqual(status["final_attempts_remaining"], 0)

    def test_budget_status_reports_time_and_attempts(self) -> None:
        status = self.evaluator.budget_status()
        self.assertEqual(status["status"], "active")
        self.assertEqual(status["final_attempts_used"], 0)
        self.assertEqual(status["final_attempts_remaining"], 3)
        self.assertGreater(status["remaining_seconds"], 0)

    def test_expired_budget_locks_session(self) -> None:
        state = self.evaluator._read_state()
        state["deadline_epoch"] = time.time() - 1
        self.evaluator._write_state(state)
        status = self.evaluator.budget_status()
        self.assertEqual(status["status"], "ended")
        self.assertEqual(status["end_reason"], "budget_limit")
        with self.assertRaises(HarnessError):
            self.evaluator.write_text_file("late.md", "late")

    def test_mcp_lists_only_five_tools(self) -> None:
        catalog = self.root / "problems.json"
        catalog.write_text(json.dumps({"problems": [self.problem]}), encoding="utf-8")
        command = [
            sys.executable,
            str(PROJECT_ROOT / "bf_harness" / "gateway.py"),
            "--catalog",
            str(catalog),
            "--tests-root",
            str(self.tests_root),
            "--artifacts",
            str(self.artifacts),
            "--state-root",
            str(self.root / "gateway-state"),
            "--problem",
            "T01",
            "--interpreter",
            str(PROJECT_ROOT / "tritium"),
            "--max-final-attempts",
            "3",
            "--deadline-epoch",
            str(time.time() + 60),
        ]
        messages = "\n".join(
            [
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {"protocolVersion": "2025-06-18"},
                    }
                ),
                json.dumps(
                    {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
                ),
                "",
            ]
        )
        completed = subprocess.run(
            command,
            input=messages,
            text=True,
            capture_output=True,
            timeout=5,
            check=True,
        )
        responses = [json.loads(line) for line in completed.stdout.splitlines()]
        self.assertIn(
            "Call these MCP tools directly", responses[0]["result"]["instructions"]
        )
        names = [tool["name"] for tool in responses[1]["result"]["tools"]]
        self.assertEqual(
            names,
            [
                "write_text_file",
                "run_brainfuck",
                "evaluate_solution",
                "submit_solution",
                "get_budget_status",
            ],
        )

    def test_export_contains_trace_and_artifacts_without_credentials(self) -> None:
        session_root = self.root / "session"
        control = session_root / "control"
        workspace = session_root / "workspace"
        artifacts = workspace / "artifacts"
        state_root = control / "state"
        for directory in (control, artifacts, state_root):
            directory.mkdir(parents=True, exist_ok=True)
        (workspace / "problem.json").write_text("{}\n", encoding="utf-8")
        (control / "prompt.txt").write_text("prompt\n", encoding="utf-8")
        (control / "agent-trace.jsonl").write_text(
            json.dumps(
                {
                    "type": "turn.completed",
                    "usage": {"input_tokens": 10, "output_tokens": 5},
                }
            )
            + "\n",
            encoding="utf-8",
        )
        (control / "client-stderr.log").write_text("", encoding="utf-8")
        (state_root / "audit.jsonl").write_text("", encoding="utf-8")
        state = {"status": "ended", "end_reason": "passed"}
        (state_root / "state.json").write_text(json.dumps(state), encoding="utf-8")
        (control / "launch.json").write_text(
            json.dumps(
                {
                    "client": "codex",
                    "client_version": "test",
                    "problem": "T01",
                    "model": "test-model",
                    "effort": "high",
                    "started_at_epoch": time.time(),
                    "time_limit_minutes": 30,
                    "max_final_attempts": 3,
                }
            ),
            encoding="utf-8",
        )
        (artifacts / "submission.bf").write_text("+.", encoding="utf-8")
        secret = self.root / "credential"
        secret.write_text("SECRET_CREDENTIAL", encoding="utf-8")
        credential = control / "codex-home" / "auth.json"
        credential.parent.mkdir()
        credential.symlink_to(secret)

        export_path = export_session(
            session_root=session_root,
            exit_code=0,
            termination_reason="passed",
            state=state,
        )

        with zipfile.ZipFile(export_path) as archive:
            names = set(archive.namelist())
            self.assertIn("agent-trace.jsonl", names)
            self.assertIn("artifacts/submission.bf", names)
            self.assertNotIn("control/codex-home/auth.json", names)
            manifest = json.loads(archive.read("manifest.json"))
            self.assertEqual(manifest["effort"], "high")
            contents = b"".join(archive.read(name) for name in names)
            self.assertNotIn(b"SECRET_CREDENTIAL", contents)

    def test_launcher_stops_client_at_time_limit(self) -> None:
        session_root = self.root / "timed-session"
        (session_root / "workspace").mkdir(parents=True)
        (session_root / "control").mkdir()
        exit_code, reason = run_client(
            command=[sys.executable, "-c", "import time; time.sleep(10)"],
            session_root=session_root,
            environment={},
            deadline_epoch=time.time() + 0.2,
        )
        self.assertNotEqual(exit_code, 0)
        self.assertEqual(reason, "budget_limit")
        state = finalize_state(session_root, reason)
        self.assertEqual(state["status"], "ended")
        self.assertEqual(state["end_reason"], "budget_limit")

    def test_launcher_marks_an_early_client_exit_as_incomplete(self) -> None:
        session_root = self.root / "incomplete-session"
        (session_root / "workspace").mkdir(parents=True)
        (session_root / "control").mkdir()
        exit_code, reason = run_client(
            command=[sys.executable, "-c", "pass"],
            session_root=session_root,
            environment={},
            deadline_epoch=time.time() + 10,
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(reason, "incomplete_client_exit")

    def test_codex_config_uses_compatible_tool_controls(self) -> None:
        config_path = self.root / "config.toml"
        write_codex_config(config_path, ["gateway.py"])
        config = tomllib.loads(config_path.read_text(encoding="utf-8"))
        self.assertNotIn("tools", config)
        self.assertEqual(config["web_search"], "disabled")
        self.assertFalse(config["features"]["view_image"])
        self.assertFalse(config["features"]["shell_tool"])
        self.assertTrue(config["features"]["code_mode"]["enabled"])
        self.assertEqual(
            config["features"]["code_mode"]["direct_only_tool_namespaces"],
            ["mcp__brainfuck"],
        )

    def test_help_lists_recommended_codex_models(self) -> None:
        completed = subprocess.run(
            [str(PROJECT_ROOT / "scripts" / "run-codex"), "--help"],
            text=True,
            capture_output=True,
            check=True,
        )
        for model in CODEX_MODELS:
            self.assertIn(model, completed.stdout)
        self.assertIn("Codex reasoning effort", completed.stdout)

    def test_codex_model_and_effort_arguments(self) -> None:
        self.assertEqual(
            codex_selection_arguments("gpt-5.6-terra", "high"),
            [
                "--model",
                "gpt-5.6-terra",
                "--config",
                'model_reasoning_effort="high"',
            ],
        )


if __name__ == "__main__":
    unittest.main()
