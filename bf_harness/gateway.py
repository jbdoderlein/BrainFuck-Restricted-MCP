from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bf_harness.evaluator import BrainfuckEvaluator, HarnessError, load_catalog


TOOLS = [
    {
        "name": "write_text_file",
        "description": (
            "Write UTF-8 text to the artifact directory. Use submission.bf for the "
            "Brainfuck solution. This tool never executes the text."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "A relative path in the artifact directory.",
                },
                "content": {
                    "type": "string",
                    "description": "The complete UTF-8 file content.",
                },
            },
            "required": ["path", "content"],
            "additionalProperties": False,
        },
    },
    {
        "name": "run_brainfuck",
        "description": (
            "Run submission.bf with the fixed Brainfuck interpreter. "
            "The input is exact and has no implicit newline."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "input": {"type": "string", "description": "Exact standard input text."}
            },
            "required": ["input"],
            "additionalProperties": False,
        },
    },
    {
        "name": "evaluate_solution",
        "description": (
            "Run the public and semi-private tests. Return the first failed test. "
            "This tool never runs the private tests."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    {
        "name": "submit_solution",
        "description": (
            "Use one final attempt. Run all test sets. Private failures reveal no test data. "
            "A pass or the last attempt ends the session."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    {
        "name": "get_budget_status",
        "description": (
            "Get the remaining session time and final attempts. "
            "This tool does not report tokens during the session."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--tests-root", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--problem", required=True)
    parser.add_argument("--interpreter", type=Path, required=True)
    parser.add_argument("--bubblewrap", type=Path, default=Path("/usr/bin/bwrap"))
    parser.add_argument("--max-final-attempts", type=int, required=True)
    parser.add_argument("--deadline-epoch", type=float, required=True)
    return parser.parse_args()


def build_evaluator(arguments: argparse.Namespace) -> BrainfuckEvaluator:
    catalog = load_catalog(arguments.catalog.resolve())
    if arguments.problem not in catalog:
        raise HarnessError("The selected problem does not exist.")
    return BrainfuckEvaluator(
        problem=catalog[arguments.problem],
        tests_root=arguments.tests_root,
        artifact_root=arguments.artifacts,
        state_root=arguments.state_root,
        interpreter=arguments.interpreter,
        bubblewrap=arguments.bubblewrap,
        max_final_attempts=arguments.max_final_attempts,
        deadline_epoch=arguments.deadline_epoch,
    )


def tool_result(result: dict[str, Any], *, is_error: bool = False) -> dict[str, Any]:
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(result, ensure_ascii=False, sort_keys=True),
            }
        ],
        "isError": is_error,
    }


def call_tool(
    evaluator: BrainfuckEvaluator, name: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    if name == "write_text_file":
        if not isinstance(arguments.get("path"), str) or not isinstance(
            arguments.get("content"), str
        ):
            raise HarnessError("The path and content must be text.")
        return evaluator.write_text_file(arguments["path"], arguments["content"])
    if name == "run_brainfuck":
        if not isinstance(arguments.get("input"), str):
            raise HarnessError("The input must be text.")
        return evaluator.run(arguments["input"])
    if name == "evaluate_solution":
        return evaluator.evaluate()
    if name == "submit_solution":
        return evaluator.submit()
    if name == "get_budget_status":
        return evaluator.budget_status()
    raise HarnessError("The requested tool does not exist.")


def respond(message: dict[str, Any]) -> None:
    sys.stdout.write(
        json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n"
    )
    sys.stdout.flush()


def main() -> int:
    try:
        evaluator = build_evaluator(parse_args())
    except Exception as exc:
        sys.stderr.write(f"Gateway startup failed: {exc}\n")
        return 1

    for line in sys.stdin:
        try:
            request = json.loads(line)
            method = request.get("method")
            request_id = request.get("id")
            if (
                method == "notifications/initialized"
                or method == "notifications/cancelled"
            ):
                continue
            if method == "exit":
                return 0
            if request_id is None:
                continue
            if method == "initialize":
                requested_version = request.get("params", {}).get(
                    "protocolVersion", "2024-11-05"
                )
                result = {
                    "protocolVersion": requested_version,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "brainfuck-evaluation", "version": "0.1.0"},
                    "instructions": (
                        "Call these MCP tools directly. Do not use Code Mode or a shell. "
                        "Write submission.bf with write_text_file. Use evaluate_solution "
                        "before submit_solution."
                    ),
                }
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": TOOLS}
            elif method == "tools/call":
                params = request.get("params", {})
                try:
                    value = call_tool(
                        evaluator, params.get("name", ""), params.get("arguments", {})
                    )
                    result = tool_result(value)
                except HarnessError as exc:
                    result = tool_result(
                        {"status": "error", "message": str(exc)}, is_error=True
                    )
            elif method == "shutdown":
                result = None
            else:
                respond(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {
                            "code": -32601,
                            "message": "The requested method does not exist.",
                        },
                    }
                )
                continue
            respond({"jsonrpc": "2.0", "id": request_id, "result": result})
        except Exception as exc:
            request_id = locals().get("request", {}).get("id")
            respond(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {
                        "code": -32603,
                        "message": f"The gateway failed: {type(exc).__name__}.",
                    },
                }
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
