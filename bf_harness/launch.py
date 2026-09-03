from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bf_harness.evaluator import FIXED_RUNTIME_CONTRACT, HarnessError, load_catalog


TOOL_NAMES = [
    "write_text_file",
    "run_brainfuck",
    "evaluate_solution",
    "submit_solution",
    "get_budget_status",
]

FINAL_RESULT_GRACE_SECONDS = 5.0
CODEX_MODELS = ("gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna")
REASONING_EFFORTS = ("low", "medium", "high", "xhigh", "max")


def parse_args() -> argparse.Namespace:
    project_root = Path(__file__).resolve().parents[1]
    client_hint = sys.argv[1] if len(sys.argv) > 1 else ""
    if client_hint == "codex":
        model_help = f"Select a Codex model. Recommended: {', '.join(CODEX_MODELS)}."
        effort_help = (
            "Set the Codex reasoning effort. "
            f"Valid levels: {', '.join(REASONING_EFFORTS)}."
        )
    elif client_hint == "claude":
        model_help = "Select a Claude model or alias, such as sonnet or opus."
        effort_help = (
            "Set the Claude Code reasoning effort. "
            f"Valid levels: {', '.join(REASONING_EFFORTS)}."
        )
    else:
        model_help = "Select the model."
        effort_help = f"Set the reasoning effort: {', '.join(REASONING_EFFORTS)}."
    parser = argparse.ArgumentParser(
        description="Start one restricted Brainfuck evaluation.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Codex examples:\n"
            "  ./scripts/run-codex E02 --model gpt-5.6-sol --effort medium\n"
            "  ./scripts/run-codex E02 --model gpt-5.6-terra --effort high"
            if client_hint == "codex"
            else None
        ),
    )
    parser.add_argument("client", choices=["codex", "claude"])
    parser.add_argument("problem")
    parser.add_argument(
        "--catalog",
        type=Path,
        default=project_root / "data" / "problems.json",
    )
    parser.add_argument(
        "--tests-root",
        type=Path,
        default=project_root / "data" / "tests",
    )
    parser.add_argument(
        "--sessions-root",
        type=Path,
        default=project_root / ".sessions",
    )
    parser.add_argument("--interpreter", type=Path, default=project_root / "tritium")
    parser.add_argument("--model", help=model_help)
    parser.add_argument(
        "--effort",
        choices=REASONING_EFFORTS,
        help=effort_help,
    )
    parser.add_argument("--final-attempts", type=int, default=3)
    parser.add_argument("--time-limit-minutes", type=float, default=30.0)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def agent_prompt(
    problem: dict[str, Any], *, final_attempts: int, time_limit_minutes: float
) -> str:
    public_problem = dict(problem)
    public_problem["runtime"] = {**FIXED_RUNTIME_CONTRACT, **problem.get("runtime", {})}
    public_problem_json = json.dumps(public_problem, ensure_ascii=False, indent=2)
    return f"""You must solve one programming problem in Brainfuck.

You can write any UTF-8 text file in the artifact directory.
Write the final Brainfuck program to submission.bf.
Text files never execute as system programs.

Use run_brainfuck to run submission.bf with an exact input.
Use evaluate_solution during development.
That tool checks the public and semi-private tests.
It returns the first failed test.

Call the mcp__brainfuck tools directly.
Do not use Code Mode or a shell tool.
Ignore a notice that says Code Mode is unavailable.

You have {final_attempts} final attempts.
The session time limit is {time_limit_minutes:g} minutes.
Use get_budget_status to get the remaining time and final attempts.

Call submit_solution only when the solution is ready.
Each call uses one final attempt and runs the private tests.
A private failure does not reveal the test data.
You can continue after a failed attempt if an attempt remains.
After a failed submit_solution call, use get_budget_status before deciding what to do next.
Check how many final attempts and how much time remain, then plan accordingly.
The session ends after a pass, the last attempt, or the time limit.

Do not submit before the development tests pass.

The problem's runtime block below is the exact interpreter contract, not a suggestion.
Cells are 8-bit and wrap silently on overflow (256 becomes 0, no error).
Reading input at end of file yields 0, not -1 and not an unchanged cell.
Moving the tape pointer below position 0 is a runtime error, not wraparound.
Standard input is exactly the given text with no trailing newline appended.
Expected output is compared byte for byte, so do not emit a trailing newline or trailing separator unless the expected output shows one.
Test input and output are ASCII text unless a problem says otherwise.

Problem:
{public_problem_json}
"""


def gateway_arguments(
    *,
    project_root: Path,
    catalog: Path,
    tests_root: Path,
    workspace: Path,
    control: Path,
    problem_id: str,
    interpreter: Path,
    max_final_attempts: int,
    deadline_epoch: float,
) -> list[str]:
    return [
        str(project_root / "bf_harness" / "gateway.py"),
        "--catalog",
        str(catalog),
        "--tests-root",
        str(tests_root),
        "--artifacts",
        str(workspace / "artifacts"),
        "--state-root",
        str(control / "state"),
        "--problem",
        problem_id,
        "--interpreter",
        str(interpreter),
        "--bubblewrap",
        "/usr/bin/bwrap",
        "--max-final-attempts",
        str(max_final_attempts),
        "--deadline-epoch",
        str(deadline_epoch),
    ]


def toml_string(value: str) -> str:
    return json.dumps(value)


def file_sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def executable_version(executable: str) -> str:
    completed = subprocess.run(
        [executable, "--version"],
        text=True,
        capture_output=True,
        timeout=5,
        check=False,
    )
    return (completed.stdout or completed.stderr).strip()


def codex_selection_arguments(model: str | None, effort: str | None) -> list[str]:
    arguments: list[str] = []
    if model:
        arguments.extend(["--model", model])
    if effort:
        arguments.extend(["--config", f'model_reasoning_effort="{effort}"'])
    return arguments


def isolated_gateway_arguments(gateway_args: list[str]) -> list[str]:
    return ["-i", "PATH=/usr/bin", sys.executable, *gateway_args]


def write_codex_config(path: Path, gateway_args: list[str]) -> None:
    argument_list = ", ".join(
        toml_string(value) for value in isolated_gateway_arguments(gateway_args)
    )
    tool_list = ", ".join(toml_string(value) for value in TOOL_NAMES)
    config = f"""approval_policy = "never"
sandbox_mode = "read-only"
web_search = "disabled"
allow_login_shell = false
check_for_update_on_startup = false
suppress_unstable_features_warning = true

[features]
apps = false
browser_use = false
browser_use_external = false
browser_use_full_cdp_access = false
code_mode_host = false
computer_use = false
goals = false
hooks = false
image_generation = false
in_app_browser = false
in_app_chat = false
in_app_dictation = false
in_app_local_automation = false
in_app_updates = false
multi_agent = false
plugins = false
plugin_sharing = false
remote_plugin = false
shell_tool = false
shell_snapshot = false
skill_mcp_dependency_install = false
skill_search = false
tool_suggest = false
unified_exec = false
view_image = false
workspace_dependencies = false

[features.code_mode]
enabled = true
direct_only_tool_namespaces = ["mcp__brainfuck"]

[mcp_servers.brainfuck]
command = "/usr/bin/env"
args = [{argument_list}]
required = true
enabled_tools = [{tool_list}]
default_tools_approval_mode = "approve"
startup_timeout_sec = 10
tool_timeout_sec = 30
"""
    path.write_text(config, encoding="utf-8")
    os.chmod(path, 0o600)


def write_claude_files(
    mcp_path: Path,
    settings_path: Path,
    gateway_args: list[str],
) -> None:
    mcp_config = {
        "mcpServers": {
            "brainfuck": {
                "type": "stdio",
                "command": "/usr/bin/env",
                "args": isolated_gateway_arguments(gateway_args),
            }
        }
    }
    settings = {
        "permissions": {
            "defaultMode": "dontAsk",
            "disableBypassPermissionsMode": "disable",
            "disableAutoMode": "disable",
        },
        "disableClaudeAiConnectors": True,
    }
    mcp_path.write_text(json.dumps(mcp_config, indent=2) + "\n", encoding="utf-8")
    settings_path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    os.chmod(mcp_path, 0o600)
    os.chmod(settings_path, 0o600)


def prepare_session(
    arguments: argparse.Namespace,
) -> tuple[Path, dict[str, Any], list[str], dict[str, str], float]:
    project_root = Path(__file__).resolve().parents[1]
    if arguments.final_attempts <= 0:
        raise HarnessError("The final-attempt limit must be positive.")
    if arguments.time_limit_minutes <= 0:
        raise HarnessError("The session time limit must be positive.")
    started_at_epoch = time.time()
    deadline_epoch = started_at_epoch + arguments.time_limit_minutes * 60
    catalog_path = arguments.catalog.resolve()
    tests_root = arguments.tests_root.resolve()
    interpreter = arguments.interpreter.resolve()
    catalog = load_catalog(catalog_path)
    if arguments.problem not in catalog:
        raise HarnessError("The selected problem does not exist.")
    if not interpreter.is_file():
        raise HarnessError("The Brainfuck interpreter does not exist.")
    if not Path("/usr/bin/bwrap").is_file():
        raise HarnessError(
            "Bubblewrap does not exist. The launcher refuses to continue."
        )

    session_name = f"{time.strftime('%Y%m%d-%H%M%S')}-{arguments.client}-{arguments.problem}-{uuid.uuid4().hex[:8]}"
    session_root = arguments.sessions_root.resolve() / session_name
    workspace = session_root / "workspace"
    control = session_root / "control"
    artifacts = workspace / "artifacts"
    for directory in (session_root, workspace, control, artifacts):
        directory.mkdir(
            parents=True,
            exist_ok=False if directory == session_root else True,
            mode=0o700,
        )
        os.chmod(directory, 0o700)

    problem = catalog[arguments.problem]
    (workspace / "problem.json").write_text(
        json.dumps(problem, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.chmod(workspace / "problem.json", 0o400)
    prompt = agent_prompt(
        problem,
        final_attempts=arguments.final_attempts,
        time_limit_minutes=arguments.time_limit_minutes,
    )
    (control / "prompt.txt").write_text(prompt, encoding="utf-8")
    os.chmod(control / "prompt.txt", 0o600)

    gateway_args = gateway_arguments(
        project_root=project_root,
        catalog=catalog_path,
        tests_root=tests_root,
        workspace=workspace,
        control=control,
        problem_id=arguments.problem,
        interpreter=interpreter,
        max_final_attempts=arguments.final_attempts,
        deadline_epoch=deadline_epoch,
    )
    environment = dict(os.environ)

    if arguments.client == "codex":
        executable = shutil.which("codex")
        if executable is None:
            raise HarnessError("The Codex executable does not exist.")
        original_codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
        codex_home = control / "codex-home"
        codex_home.mkdir(mode=0o700)
        auth_source = original_codex_home / "auth.json"
        if auth_source.is_file() and not arguments.dry_run:
            (codex_home / "auth.json").symlink_to(auth_source)
        write_codex_config(codex_home / "config.toml", gateway_args)
        environment["CODEX_HOME"] = str(codex_home)
        command = [
            executable,
            "exec",
            "--strict-config",
            "--skip-git-repo-check",
            "--ephemeral",
            "--json",
            "-C",
            str(workspace),
        ]
        command.extend(codex_selection_arguments(arguments.model, arguments.effort))
        command.append(prompt)
    else:
        executable = shutil.which("claude")
        if executable is None:
            raise HarnessError("The Claude Code executable does not exist.")
        original_claude_home = Path(
            os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude")
        )
        claude_home = control / "claude-home"
        claude_home.mkdir(mode=0o700)
        credentials_source = original_claude_home / ".credentials.json"
        if credentials_source.is_file() and not arguments.dry_run:
            (claude_home / ".credentials.json").symlink_to(credentials_source)
        environment["CLAUDE_CONFIG_DIR"] = str(claude_home)
        mcp_path = control / "claude-mcp.json"
        settings_path = control / "claude-settings.json"
        write_claude_files(mcp_path, settings_path, gateway_args)
        allowed_tools = ",".join(f"mcp__brainfuck__{name}" for name in TOOL_NAMES)
        command = [
            executable,
            "-p",
            "--restricted",
            "--tools",
            "",
            "--strict-mcp-config",
            "--mcp-config",
            str(mcp_path),
            "--settings",
            str(settings_path),
            "--permission-mode",
            "dontAsk",
            "--allowedTools",
            allowed_tools,
            "--disable-slash-commands",
            "--no-chrome",
            "--no-session-persistence",
            "--output-format",
            "stream-json",
            "--include-partial-messages",
            "--verbose",
        ]
        if arguments.model:
            command.extend(["--model", arguments.model])
        if arguments.effort:
            command.extend(["--effort", arguments.effort])
        command.append(prompt)

    metadata = {
        "client": arguments.client,
        "client_version": executable_version(executable),
        "problem": arguments.problem,
        "model": arguments.model,
        "effort": arguments.effort,
        "cwd": str(workspace),
        "catalog_sha256": file_sha256(catalog_path),
        "interpreter_sha256": file_sha256(interpreter),
        "started_at_epoch": started_at_epoch,
        "deadline_epoch": deadline_epoch,
        "time_limit_minutes": arguments.time_limit_minutes,
        "max_final_attempts": arguments.final_attempts,
        "command": command,
    }
    (control / "launch.json").write_text(
        json.dumps(metadata, indent=2) + "\n", encoding="utf-8"
    )
    os.chmod(control / "launch.json", 0o600)
    return session_root, problem, command, environment, deadline_epoch


def read_json_object(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else {}


def stop_process_group(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)


def run_client(
    *,
    command: list[str],
    session_root: Path,
    environment: dict[str, str],
    deadline_epoch: float,
) -> tuple[int, str]:
    control = session_root / "control"
    state_path = control / "state" / "state.json"
    trace_path = control / "agent-trace.jsonl"
    stderr_path = control / "client-stderr.log"
    terminal_seen_at: float | None = None
    termination_reason = "incomplete_client_exit"

    with trace_path.open("wb") as trace_stream, stderr_path.open("wb") as stderr_stream:
        os.chmod(trace_path, 0o600)
        os.chmod(stderr_path, 0o600)
        process = subprocess.Popen(
            command,
            cwd=session_root / "workspace",
            env=environment,
            stdout=trace_stream,
            stderr=stderr_stream,
            start_new_session=True,
        )
        while process.poll() is None:
            now = time.time()
            state = read_json_object(state_path)
            end_reason = state.get("end_reason")
            if now >= deadline_epoch or end_reason == "budget_limit":
                termination_reason = "budget_limit"
                stop_process_group(process)
                break
            if state.get("status") == "ended":
                termination_reason = str(end_reason or "session_ended")
                if terminal_seen_at is None:
                    terminal_seen_at = now
                elif now - terminal_seen_at >= FINAL_RESULT_GRACE_SECONDS:
                    stop_process_group(process)
                    break
            time.sleep(0.1)
        return_code = process.wait()
        state = read_json_object(state_path)
        if state.get("status") == "ended" and state.get("end_reason"):
            termination_reason = str(state["end_reason"])
    return return_code, termination_reason


def finalize_state(session_root: Path, end_reason: str) -> dict[str, Any]:
    state_path = session_root / "control" / "state" / "state.json"
    state = read_json_object(state_path)
    if state.get("status") != "ended":
        state["status"] = "ended"
        state["end_reason"] = end_reason
        state["ended_at_epoch"] = time.time()
        state_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
        os.chmod(temporary, 0o600)
        os.replace(temporary, state_path)
    return state


def reported_usage(trace_path: Path) -> dict[str, Any] | None:
    if not trace_path.is_file():
        return None
    latest: dict[str, Any] | None = None
    with trace_path.open(encoding="utf-8", errors="replace") as stream:
        for line in stream:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict) or not isinstance(event.get("usage"), dict):
                continue
            if event.get("type") in {"turn.completed", "result"}:
                latest = {
                    "source_event": event["type"],
                    "usage": event["usage"],
                }
    return latest


def export_session(
    *,
    session_root: Path,
    exit_code: int,
    termination_reason: str,
    state: dict[str, Any],
) -> Path:
    control = session_root / "control"
    launch = read_json_object(control / "launch.json")
    ended_at_epoch = time.time()
    usage = reported_usage(control / "agent-trace.jsonl")
    manifest = {
        "format_version": 1,
        "session_id": session_root.name,
        "client": launch.get("client"),
        "client_version": launch.get("client_version"),
        "problem": launch.get("problem"),
        "model": launch.get("model"),
        "effort": launch.get("effort"),
        "catalog_sha256": launch.get("catalog_sha256"),
        "interpreter_sha256": launch.get("interpreter_sha256"),
        "started_at_epoch": launch.get("started_at_epoch"),
        "ended_at_epoch": ended_at_epoch,
        "duration_seconds": round(
            max(0.0, ended_at_epoch - float(launch.get("started_at_epoch", 0.0))),
            3,
        ),
        "time_limit_minutes": launch.get("time_limit_minutes"),
        "max_final_attempts": launch.get("max_final_attempts"),
        "termination_reason": termination_reason,
        "client_exit_code": exit_code,
        "reported_usage": usage,
        "session_state": state,
    }
    manifest_path = control / "export-manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.chmod(manifest_path, 0o600)

    result_path = control / "client-result.json"
    result_path.write_text(
        json.dumps(
            {
                "exit_code": exit_code,
                "termination_reason": termination_reason,
                "reported_usage": usage,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    os.chmod(result_path, 0o600)

    selected_files = {
        manifest_path: "manifest.json",
        session_root / "workspace" / "problem.json": "problem.json",
        control / "prompt.txt": "prompt.txt",
        control / "agent-trace.jsonl": "agent-trace.jsonl",
        control / "client-stderr.log": "client-stderr.log",
        control / "state" / "audit.jsonl": "tool-audit.jsonl",
        control / "state" / "state.json": "session-state.json",
        result_path: "client-result.json",
    }
    export_path = session_root.with_suffix(".zip")
    temporary_path = export_path.with_suffix(".zip.tmp")
    with zipfile.ZipFile(
        temporary_path, "w", compression=zipfile.ZIP_DEFLATED
    ) as archive:
        for source, archive_name in selected_files.items():
            if source.is_file() and not source.is_symlink():
                archive.write(source, archive_name)
        artifact_root = session_root / "workspace" / "artifacts"
        for source in sorted(artifact_root.rglob("*")):
            if source.is_file() and not source.is_symlink():
                relative = source.relative_to(artifact_root)
                archive.write(source, str(Path("artifacts") / relative))
    os.chmod(temporary_path, 0o600)
    os.replace(temporary_path, export_path)
    return export_path


def main() -> int:
    arguments = parse_args()
    try:
        session_root, _problem, command, environment, deadline_epoch = prepare_session(
            arguments
        )
    except (HarnessError, OSError, json.JSONDecodeError) as exc:
        sys.stderr.write(f"The launcher failed: {exc}\n")
        return 1

    print(f"Session: {session_root}", flush=True)
    if arguments.dry_run:
        print("Command:")
        print(shlex.join(command))
        return 0

    exit_code = 1
    termination_reason = "launcher_error"
    try:
        exit_code, termination_reason = run_client(
            command=command,
            session_root=session_root,
            environment=environment,
            deadline_epoch=deadline_epoch,
        )
    finally:
        credential_paths = [
            session_root / "control" / "codex-home" / "auth.json",
            session_root / "control" / "claude-home" / ".credentials.json",
        ]
        for credential_path in credential_paths:
            if credential_path.exists() or credential_path.is_symlink():
                credential_path.unlink()
    state = finalize_state(session_root, termination_reason)
    termination_reason = str(state.get("end_reason") or termination_reason)
    export_path = export_session(
        session_root=session_root,
        exit_code=exit_code,
        termination_reason=termination_reason,
        state=state,
    )
    print(f"Export: {export_path}", flush=True)
    if termination_reason == "passed":
        return 0
    if termination_reason == "attempt_limit":
        return 2
    if termination_reason == "budget_limit":
        return 124
    if termination_reason == "incomplete_client_exit" and exit_code == 0:
        return 3
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
