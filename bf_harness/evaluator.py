from __future__ import annotations

import json
import math
import os
import resource
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class HarnessError(RuntimeError):
    """A safe error that the MCP server can return to the agent."""


@dataclass(frozen=True)
class RuntimeLimits:
    timeout_seconds: float
    memory_bytes: int
    max_output_bytes: int
    max_source_bytes: int
    max_text_file_bytes: int
    max_artifact_bytes: int
    max_input_bytes: int
    max_artifact_files: int

    @classmethod
    def from_problem(cls, problem: dict[str, Any]) -> "RuntimeLimits":
        runtime = problem.get("runtime", {})
        return cls(
            timeout_seconds=float(runtime.get("timeout_seconds", 2.0)),
            memory_bytes=int(runtime.get("memory_bytes", 512 * 1024 * 1024)),
            max_output_bytes=int(runtime.get("max_output_bytes", 64 * 1024)),
            max_source_bytes=int(runtime.get("max_source_bytes", 1024 * 1024)),
            max_text_file_bytes=int(runtime.get("max_text_file_bytes", 1024 * 1024)),
            max_artifact_bytes=int(runtime.get("max_artifact_bytes", 4 * 1024 * 1024)),
            max_input_bytes=int(runtime.get("max_input_bytes", 1024 * 1024)),
            max_artifact_files=int(runtime.get("max_artifact_files", 128)),
        )


def load_catalog(path: Path) -> dict[str, dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    problems = data.get("problems")
    if not isinstance(problems, list):
        raise HarnessError("The problem catalog does not contain a problems list.")
    catalog: dict[str, dict[str, Any]] = {}
    for problem in problems:
        if not isinstance(problem, dict) or not isinstance(problem.get("id"), str):
            raise HarnessError("The problem catalog contains an invalid problem.")
        catalog[problem["id"]] = problem
    return catalog


def load_tests(path: Path, problem_id: str) -> list[dict[str, str]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("problem_id") != problem_id:
        raise HarnessError("The test file has an incorrect problem identifier.")
    tests = data.get("tests")
    if not isinstance(tests, list):
        raise HarnessError("The test file does not contain a tests list.")
    validated: list[dict[str, str]] = []
    for test in tests:
        if not isinstance(test, dict):
            raise HarnessError("The test file contains an invalid test.")
        test_input = test.get("input")
        expected = test.get("output")
        if not isinstance(test_input, str) or not isinstance(expected, str):
            raise HarnessError("Each test input and output must be text.")
        validated.append({"input": test_input, "output": expected})
    return validated


def _display_bytes(value: bytes) -> str:
    return value.decode("utf-8", errors="backslashreplace")


class BrainfuckEvaluator:
    def __init__(
        self,
        *,
        problem: dict[str, Any],
        tests_root: Path,
        artifact_root: Path,
        state_root: Path,
        interpreter: Path,
        bubblewrap: Path,
        max_final_attempts: int = 3,
        deadline_epoch: float | None = None,
    ) -> None:
        self.problem = problem
        self.problem_id = problem["id"]
        self.tests_root = tests_root.resolve()
        self.artifact_root = artifact_root.resolve()
        self.state_root = state_root.resolve()
        self.interpreter = interpreter.resolve()
        self.bubblewrap = bubblewrap.resolve()
        self.limits = RuntimeLimits.from_problem(problem)
        self.max_final_attempts = max_final_attempts
        self.deadline_epoch = (
            deadline_epoch if deadline_epoch is not None else time.time() + 30 * 60
        )

        if self.max_final_attempts <= 0:
            raise HarnessError("The final-attempt limit must be positive.")
        if not math.isfinite(self.deadline_epoch) or self.deadline_epoch <= 0:
            raise HarnessError("The session deadline is invalid.")

        runtime = problem.get("runtime", {})
        fixed_contract = {
            "cell_bits": 8,
            "cell_overflow": "wrap",
            "negative_tape": "error",
            "eof_value": 0,
            "encoding": "utf-8",
            "input_has_implicit_newline": False,
        }
        for key, expected in fixed_contract.items():
            if runtime.get(key, expected) != expected:
                raise HarnessError(f"The runtime value for {key} is not supported.")
        numeric_limits = (
            self.limits.timeout_seconds,
            self.limits.memory_bytes,
            self.limits.max_output_bytes,
            self.limits.max_source_bytes,
            self.limits.max_text_file_bytes,
            self.limits.max_artifact_bytes,
            self.limits.max_input_bytes,
            self.limits.max_artifact_files,
        )
        if any(value <= 0 for value in numeric_limits):
            raise HarnessError("All runtime limits must be positive.")

        if not self.interpreter.is_file():
            raise HarnessError("The Brainfuck interpreter does not exist.")
        if not self.bubblewrap.is_file():
            raise HarnessError(
                "Bubblewrap does not exist. The evaluator refuses to run."
            )

        self.artifact_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.state_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.artifact_root, 0o700)
        os.chmod(self.state_root, 0o700)
        self.public_tests = self._public_tests()
        self.semi_private_tests = self._suite_tests("semi_private")
        self.private_tests = self._suite_tests("private")
        if not self.public_tests:
            raise HarnessError("The public test set is empty.")
        if not self.semi_private_tests:
            raise HarnessError("The semi-private test set is empty.")
        if not self.private_tests:
            raise HarnessError("The private test set is empty.")
        self._initialize_state()

    @property
    def submission_path(self) -> Path:
        return self.artifact_root / "submission.bf"

    @property
    def state_path(self) -> Path:
        return self.state_root / "state.json"

    @property
    def audit_path(self) -> Path:
        return self.state_root / "audit.jsonl"

    def _initial_state(self) -> dict[str, Any]:
        return {
            "status": "active",
            "end_reason": None,
            "started_at_epoch": time.time(),
            "deadline_epoch": self.deadline_epoch,
            "evaluation_count": 0,
            "final_attempts_used": 0,
            "max_final_attempts": self.max_final_attempts,
        }

    def _initialize_state(self) -> None:
        if self.state_path.exists():
            state = self._read_state()
            if int(state.get("max_final_attempts", 0)) != self.max_final_attempts:
                raise HarnessError("The session attempt limit changed.")
            if float(state.get("deadline_epoch", 0.0)) != self.deadline_epoch:
                raise HarnessError("The session deadline changed.")
            return
        self._write_state(self._initial_state())

    def _read_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            raise HarnessError("The session state does not exist.")
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def _write_state(self, state: dict[str, Any]) -> None:
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
        os.chmod(temporary, 0o600)
        os.replace(temporary, self.state_path)

    def _audit(self, event: str, result: dict[str, Any]) -> None:
        record = {
            "time": time.time(),
            "event": event,
            "problem_id": self.problem_id,
            "result": result,
        }
        with self.audit_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=True, sort_keys=True) + "\n")
        os.chmod(self.audit_path, 0o600)

    def _require_open_session(self) -> dict[str, Any]:
        state = self._read_state()
        if state.get("status") != "active":
            raise HarnessError("The session ended. No more actions are permitted.")
        if time.time() >= float(state["deadline_epoch"]):
            state["status"] = "ended"
            state["end_reason"] = "budget_limit"
            state["ended_at_epoch"] = time.time()
            self._write_state(state)
            raise HarnessError("The session time budget expired.")
        return state

    def budget_status(self) -> dict[str, Any]:
        state = self._read_state()
        now = time.time()
        if state.get("status") == "active" and now >= float(state["deadline_epoch"]):
            state["status"] = "ended"
            state["end_reason"] = "budget_limit"
            state["ended_at_epoch"] = now
            self._write_state(state)
        used = int(state["final_attempts_used"])
        maximum = int(state["max_final_attempts"])
        result = {
            "status": state["status"],
            "end_reason": state.get("end_reason"),
            "elapsed_seconds": round(max(0.0, now - state["started_at_epoch"]), 1),
            "remaining_seconds": round(max(0.0, state["deadline_epoch"] - now), 1),
            "final_attempts_used": used,
            "final_attempts_remaining": max(0, maximum - used),
            "max_final_attempts": maximum,
        }
        self._audit("get_budget_status", result)
        return result

    def _safe_artifact_path(self, relative_path: str) -> Path:
        if not relative_path or "\x00" in relative_path:
            raise HarnessError("The file path is invalid.")
        candidate_input = Path(relative_path)
        if (
            candidate_input.is_absolute()
            or not candidate_input.parts
            or any(part in {"", ".", ".."} for part in candidate_input.parts)
        ):
            raise HarnessError("Use a relative file path without dot components.")
        if len(relative_path.encode("utf-8")) > 240 or len(candidate_input.parts) > 8:
            raise HarnessError("The file path is too long.")
        candidate = self.artifact_root.joinpath(*candidate_input.parts)
        current = self.artifact_root
        for part in candidate_input.parts[:-1]:
            current = current / part
            if current.exists() and current.is_symlink():
                raise HarnessError("The file path contains a symbolic link.")
        resolved_parent = candidate.parent.resolve(strict=False)
        try:
            resolved_parent.relative_to(self.artifact_root)
        except ValueError as exc:
            raise HarnessError("The file path leaves the artifact directory.") from exc
        return candidate

    def write_text_file(self, relative_path: str, content: str) -> dict[str, Any]:
        self._require_open_session()
        encoded = content.encode("utf-8")
        if len(encoded) > self.limits.max_text_file_bytes:
            raise HarnessError("The text file is too large.")
        target = self._safe_artifact_path(relative_path)
        if target.exists() and target.is_dir():
            raise HarnessError("The target path is a directory.")
        existing_size = (
            target.stat().st_size if target.exists() and not target.is_symlink() else 0
        )
        existing_files = [
            item
            for item in self.artifact_root.rglob("*")
            if item.is_file() and not item.is_symlink()
        ]
        if (
            not target.exists()
            and len(existing_files) >= self.limits.max_artifact_files
        ):
            raise HarnessError("The artifact file limit was reached.")
        total_size = sum(item.stat().st_size for item in existing_files)
        if total_size - existing_size + len(encoded) > self.limits.max_artifact_bytes:
            raise HarnessError("The artifact storage limit was reached.")
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary_fd, temporary_name = tempfile.mkstemp(
            prefix=".write-", dir=target.parent
        )
        try:
            with os.fdopen(temporary_fd, "wb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary_name, 0o600)
            os.replace(temporary_name, target)
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)
        result = {"status": "written", "path": relative_path, "bytes": len(encoded)}
        self._audit("write_text_file", result)
        return result

    def _resource_limits(self) -> None:
        cpu_seconds = max(1, math.ceil(self.limits.timeout_seconds))
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 1))
        resource.setrlimit(
            resource.RLIMIT_AS, (self.limits.memory_bytes, self.limits.memory_bytes)
        )
        resource.setrlimit(
            resource.RLIMIT_FSIZE,
            (self.limits.max_output_bytes, self.limits.max_output_bytes),
        )
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        resource.setrlimit(resource.RLIMIT_NOFILE, (32, 32))

    def _sandbox_command(self) -> list[str]:
        command = [
            str(self.bubblewrap),
            "--unshare-all",
            "--die-with-parent",
            "--new-session",
            "--clearenv",
            "--cap-drop",
            "ALL",
            "--dir",
            "/runner",
            "--dir",
            "/work",
        ]
        for library_path in ("/usr/lib", "/usr/lib64", "/lib", "/lib64"):
            source = Path(library_path)
            if source.exists():
                command.extend(["--ro-bind", str(source.resolve()), library_path])
        command.extend(
            [
                "--ro-bind",
                str(self.interpreter),
                "/runner/tritium",
                "--ro-bind",
                str(self.submission_path),
                "/work/submission.bf",
                "--proc",
                "/proc",
                "--dev",
                "/dev",
                "--tmpfs",
                "/tmp",
                "--chdir",
                "/work",
                "/runner/tritium",
                "-r",
                "-m",
                "-b",
                "-B",
                "-z",
                "-fno-negtape",
                "/work/submission.bf",
            ]
        )
        return command

    def _run_program(self, input_text: str, *, audit: bool = True) -> dict[str, Any]:
        if not self.submission_path.is_file() or self.submission_path.is_symlink():
            raise HarnessError("Write submission.bf before you run it.")
        if self.submission_path.stat().st_size > self.limits.max_source_bytes:
            raise HarnessError("The Brainfuck source file is too large.")

        input_bytes = input_text.encode("utf-8")
        with (
            tempfile.TemporaryFile() as stdout_file,
            tempfile.TemporaryFile() as stderr_file,
        ):
            try:
                completed = subprocess.run(
                    self._sandbox_command(),
                    input=input_bytes,
                    stdout=stdout_file,
                    stderr=stderr_file,
                    timeout=self.limits.timeout_seconds,
                    check=False,
                    close_fds=True,
                    start_new_session=True,
                    preexec_fn=self._resource_limits,
                )
                return_code = completed.returncode
            except subprocess.TimeoutExpired:
                result = {
                    "status": "timeout",
                    "message": "The Brainfuck program exceeded the time limit.",
                }
                if audit:
                    self._audit("run_brainfuck", result)
                return result

            stdout_file.seek(0)
            stderr_file.seek(0)
            output = stdout_file.read(self.limits.max_output_bytes + 1)
            error_output = stderr_file.read(self.limits.max_output_bytes + 1)

        output_limit_reached = (
            len(output) >= self.limits.max_output_bytes and return_code != 0
        )
        if output_limit_reached or return_code == -signal.SIGXFSZ:
            result = {
                "status": "output_limit",
                "message": "The Brainfuck program exceeded the output limit.",
            }
        elif return_code != 0:
            result = {
                "status": "runtime_error",
                "message": "The Brainfuck interpreter reported an error.",
                "stderr": _display_bytes(error_output),
            }
        else:
            result = {"status": "completed", "output": _display_bytes(output)}
        if audit:
            self._audit("run_brainfuck", result)
        return result

    def run(self, input_text: str) -> dict[str, Any]:
        self._require_open_session()
        if len(input_text.encode("utf-8")) > self.limits.max_input_bytes:
            raise HarnessError("The Brainfuck input is too large.")
        return self._run_program(input_text)

    def _public_tests(self) -> list[dict[str, str]]:
        examples = self.problem.get("input_output_examples", [])
        if not isinstance(examples, list):
            raise HarnessError("The public test list is invalid.")
        tests: list[dict[str, str]] = []
        for item in examples:
            if not isinstance(item, dict):
                raise HarnessError("The public test list contains an invalid test.")
            test_input = item.get("input")
            expected = item.get("output")
            if not isinstance(test_input, str) or not isinstance(expected, str):
                raise HarnessError("Each public test input and output must be text.")
            tests.append({"input": test_input, "output": expected})
        return tests

    def _suite_tests(self, suite: str) -> list[dict[str, str]]:
        return load_tests(
            self.tests_root / suite / f"{self.problem_id}.json", self.problem_id
        )

    def _failure(
        self, suite: str, test: dict[str, str], run_result: dict[str, Any]
    ) -> dict[str, Any]:
        actual = (
            run_result.get("output")
            if run_result.get("status") == "completed"
            else None
        )
        return {
            "status": "failed",
            "test_set": suite,
            "input": test["input"],
            "expected": test["output"],
            "actual": actual,
            "execution_status": run_result["status"],
            "message": f"A {suite.replace('_', '-')} test failed.",
        }

    def _run_visible_suite(
        self,
        suite: str,
        tests: list[dict[str, str]],
        *,
        final: bool = False,
    ) -> dict[str, Any] | None:
        expected_key = "output"
        for test in tests:
            self._require_open_session()
            run_result = (
                self._run_program(test["input"]) if final else self.run(test["input"])
            )
            if (
                run_result.get("status") != "completed"
                or run_result.get("output") != test[expected_key]
            ):
                return self._failure(suite, test, run_result)
        return None

    def evaluate(self) -> dict[str, Any]:
        state = self._require_open_session()
        state["evaluation_count"] = int(state.get("evaluation_count", 0)) + 1
        self._write_state(state)
        failure = self._run_visible_suite("public", self.public_tests)
        if failure is None:
            failure = self._run_visible_suite("semi_private", self.semi_private_tests)
        result = failure or {
            "status": "passed",
            "test_sets": ["public", "semi_private"],
            "message": "All development tests passed. The private tests did not run.",
        }
        self._audit("evaluate_solution", result)
        return result

    def submit(self) -> dict[str, Any]:
        state = self._require_open_session()
        state["final_attempts_used"] = int(state["final_attempts_used"]) + 1
        self._write_state(state)
        try:
            failure = self._run_visible_suite("public", self.public_tests, final=True)
            if failure is None:
                failure = self._run_visible_suite(
                    "semi_private", self.semi_private_tests, final=True
                )
            if failure is not None:
                result = failure
            else:
                private_failed = any(
                    not self._private_test_passes(test) for test in self.private_tests
                )
                if private_failed:
                    result = {
                        "status": "failed",
                        "test_set": "private",
                        "message": "A hidden test failed.",
                    }
                else:
                    result = {"status": "passed", "message": "All tests passed."}
        except HarnessError:
            state = self._read_state()
            if (
                state.get("status") == "active"
                and int(state["final_attempts_used"]) >= self.max_final_attempts
            ):
                state["status"] = "ended"
                state["end_reason"] = "attempt_limit"
                state["ended_at_epoch"] = time.time()
                self._write_state(state)
            raise
        state = self._read_state()
        attempts_used = int(state["final_attempts_used"])
        attempts_remaining = max(0, self.max_final_attempts - attempts_used)
        if result["status"] == "passed":
            state["status"] = "ended"
            state["end_reason"] = "passed"
            state["ended_at_epoch"] = time.time()
        elif attempts_remaining == 0:
            state["status"] = "ended"
            state["end_reason"] = "attempt_limit"
            state["ended_at_epoch"] = time.time()
        self._write_state(state)
        result["final_attempts_used"] = attempts_used
        result["final_attempts_remaining"] = attempts_remaining
        result["session_ended"] = state["status"] == "ended"
        self._audit("submit_solution", result)
        return result

    def _private_test_passes(self, test: dict[str, str]) -> bool:
        self._require_open_session()
        run_result = self._run_program(test["input"], audit=False)
        return (
            run_result.get("status") == "completed"
            and run_result.get("output") == test["output"]
        )
