from __future__ import annotations

import argparse
import json
import mimetypes
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse


PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATIC_ROOT = PROJECT_ROOT / "viewer"

OPENAI_PRICING_URL = "https://platform.openai.com/docs/pricing"
ANTHROPIC_PRICING_URL = "https://platform.claude.com/docs/en/about-claude/pricing"
PRICING_DATE = "2026-09-03"

# Prices are USD per one million tokens for standard API processing.
MODEL_PRICES: dict[str, dict[str, float | str]] = {
    "gpt-5.6-sol": {"input": 4, "cached": 0.4, "output": 20, "source": OPENAI_PRICING_URL},
    "gpt-5.6-terra": {"input": 2, "cached": 0.2, "output": 12, "source": OPENAI_PRICING_URL},
    "gpt-5.6-luna": {"input": 0.2, "cached": 0.02, "output": 1.2, "source": OPENAI_PRICING_URL},
    "gpt-5.5": {"input": 5, "cached": 0.5, "output": 30, "source": OPENAI_PRICING_URL},
    "gpt-5.4": {"input": 2.5, "cached": 0.25, "output": 15, "source": OPENAI_PRICING_URL},
    "gpt-5.3-codex": {"input": 1.75, "cached": 0.175, "output": 14, "source": OPENAI_PRICING_URL},
    "gpt-5.2": {"input": 1.75, "cached": 0.175, "output": 14, "source": OPENAI_PRICING_URL},
    "gpt-5.1": {"input": 1.25, "cached": 0.125, "output": 10, "source": OPENAI_PRICING_URL},
    "gpt-5": {"input": 1.25, "cached": 0.125, "output": 10, "source": OPENAI_PRICING_URL},
    "claude-opus-5": {"input": 5, "cached": 0.5, "cache_write_5m": 6.25, "cache_write_1h": 10, "output": 25, "source": ANTHROPIC_PRICING_URL},
    "claude-opus-4.8": {"input": 5, "cached": 0.5, "cache_write_5m": 6.25, "cache_write_1h": 10, "output": 25, "source": ANTHROPIC_PRICING_URL},
    "claude-opus-4.7": {"input": 5, "cached": 0.5, "cache_write_5m": 6.25, "cache_write_1h": 10, "output": 25, "source": ANTHROPIC_PRICING_URL},
    "claude-opus-4.6": {"input": 5, "cached": 0.5, "cache_write_5m": 6.25, "cache_write_1h": 10, "output": 25, "source": ANTHROPIC_PRICING_URL},
    "claude-opus-4.5": {"input": 5, "cached": 0.5, "cache_write_5m": 6.25, "cache_write_1h": 10, "output": 25, "source": ANTHROPIC_PRICING_URL},
    "claude-sonnet-5": {"input": 2, "cached": 0.2, "cache_write_5m": 2.5, "cache_write_1h": 4, "output": 10, "source": ANTHROPIC_PRICING_URL},
    "claude-sonnet-4.6": {"input": 3, "cached": 0.3, "cache_write_5m": 3.75, "cache_write_1h": 6, "output": 15, "source": ANTHROPIC_PRICING_URL},
    "claude-sonnet-4.5": {"input": 3, "cached": 0.3, "cache_write_5m": 3.75, "cache_write_1h": 6, "output": 15, "source": ANTHROPIC_PRICING_URL},
    "claude-haiku-4.5": {"input": 1, "cached": 0.1, "cache_write_5m": 1.25, "cache_write_1h": 2, "output": 5, "source": ANTHROPIC_PRICING_URL},
}


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def text_from_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        text = block.get("text")
        if isinstance(text, str):
            parts.append(text)
    return "\n".join(parts)


def readable_tool_name(name: str) -> str:
    return name.removeprefix("mcp__brainfuck__").replace("_", " ").title()


def aggregate_usage(messages: dict[str, dict[str, Any]]) -> dict[str, Any]:
    keys = (
        "input_tokens",
        "cached_input_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
        "output_tokens",
        "reasoning_output_tokens",
    )
    total = {key: 0 for key in keys}
    cache_creation = {
        "ephemeral_5m_input_tokens": 0,
        "ephemeral_1h_input_tokens": 0,
    }
    for usage in messages.values():
        for key in keys:
            value = usage.get(key)
            if isinstance(value, (int, float)):
                total[key] += value
        estimated_output = usage.get("_estimated_output_tokens")
        if isinstance(estimated_output, (int, float)):
            total["output_tokens"] += max(0, estimated_output - float(usage.get("output_tokens") or 0))
        breakdown = usage.get("cache_creation")
        if isinstance(breakdown, dict):
            for key in cache_creation:
                value = breakdown.get(key)
                if isinstance(value, (int, float)):
                    cache_creation[key] += value
    if any(cache_creation.values()):
        total["cache_creation"] = cache_creation
    return {key: value for key, value in total.items() if value}


def pi_usage_to_common(usage: dict[str, Any]) -> dict[str, Any]:
    mapping = {
        "input": "input_tokens",
        "output": "output_tokens",
        "cacheRead": "cache_read_input_tokens",
        "cacheWrite": "cache_creation_input_tokens",
    }
    converted: dict[str, Any] = {}
    for pi_key, common_key in mapping.items():
        value = usage.get(pi_key)
        if isinstance(value, (int, float)):
            converted[common_key] = value
    return converted


def normalized_model(model: str) -> str:
    return model.lower().split("[", 1)[0].strip()


def price_for_model(model: str) -> tuple[str, dict[str, float | str]] | None:
    normalized = normalized_model(model)
    for name in sorted(MODEL_PRICES, key=len, reverse=True):
        if normalized == name or normalized.startswith(f"{name}-"):
            return name, MODEL_PRICES[name]
    return None


def resolve_model(
    client: str, launch: dict[str, Any], trace_metadata: dict[str, Any]
) -> tuple[str, bool]:
    trace_model = trace_metadata.get("model")
    launch_model = launch.get("model")
    if isinstance(trace_model, str) and trace_model:
        return trace_model, False
    if isinstance(launch_model, str) and launch_model:
        return launch_model, False
    if client == "codex":
        return "gpt-5.6-sol", True
    return "Default", False


def estimate_api_cost(model: str, usage: dict[str, Any]) -> dict[str, Any]:
    match = price_for_model(model)
    if match is None or not usage:
        return {"available": False, "reason": "The model or token usage is not available."}
    pricing_model, prices = match
    input_tokens = float(usage.get("input_tokens") or 0)
    cached_tokens = float(usage.get("cached_input_tokens") or usage.get("cache_read_input_tokens") or 0)
    if usage.get("cached_input_tokens"):
        input_tokens = max(0, input_tokens - cached_tokens)
    output_tokens = float(usage.get("output_tokens") or 0)
    cache_write_tokens = float(usage.get("cache_creation_input_tokens") or 0)
    cache_breakdown = usage.get("cache_creation")
    cache_write_5m = cache_write_tokens
    cache_write_1h = 0.0
    if isinstance(cache_breakdown, dict):
        cache_write_5m = float(cache_breakdown.get("ephemeral_5m_input_tokens") or 0)
        cache_write_1h = float(cache_breakdown.get("ephemeral_1h_input_tokens") or 0)
        remaining = max(0, cache_write_tokens - cache_write_5m - cache_write_1h)
        cache_write_5m += remaining

    cost = input_tokens * float(prices["input"])
    cost += cached_tokens * float(prices.get("cached", prices["input"]))
    cost += output_tokens * float(prices["output"])
    cost += cache_write_5m * float(prices.get("cache_write_5m", prices["input"]))
    cost += cache_write_1h * float(prices.get("cache_write_1h", prices["input"]))
    return {
        "available": True,
        "amount_usd": cost / 1_000_000,
        "pricing_model": pricing_model,
        "pricing_date": PRICING_DATE,
        "source": prices["source"],
    }


def parse_trace(path: Path, client: str) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    events: list[dict[str, Any]] = []
    final_result = ""
    trace_metadata: dict[str, Any] = {}
    tool_names: dict[str, str] = {}
    claude_messages: dict[str, dict[str, Any]] = {}
    current_claude_message = ""
    pi_messages: dict[str, dict[str, Any]] = {}

    for raw_line in read_text(path).splitlines():
        try:
            event = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue

        event_type = event.get("type")
        if client == "codex":
            if event_type == "item.completed" and isinstance(event.get("item"), dict):
                item = event["item"]
                item_type = item.get("type")
                if item_type == "agent_message" and isinstance(item.get("text"), str):
                    final_result = item["text"]
                    events.append({"kind": "message", "title": "Agent message", "text": item["text"]})
                elif item_type == "mcp_tool_call":
                    name = str(item.get("tool") or "Tool")
                    events.append(
                        {
                            "kind": "tool",
                            "title": readable_tool_name(name),
                            "arguments": item.get("arguments"),
                            "result": item.get("result"),
                            "status": item.get("status"),
                        }
                    )
                elif item_type == "error":
                    events.append({"kind": "error", "title": "Client error", "text": str(item.get("message") or "Unknown error")})
            elif event_type == "turn.completed" and isinstance(event.get("usage"), dict):
                trace_metadata["usage"] = event["usage"]
            continue

        if client == "pi":
            if event_type == "tool_execution_start":
                tool_call_id = str(event.get("toolCallId") or "")
                name = str(event.get("toolName") or "Tool")
                tool_names[tool_call_id] = readable_tool_name(name)
                events.append(
                    {
                        "kind": "tool",
                        "title": readable_tool_name(name),
                        "arguments": event.get("args"),
                        "tool_id": tool_call_id,
                        "status": "called",
                    }
                )
            elif event_type == "tool_execution_end":
                tool_call_id = str(event.get("toolCallId") or "")
                result = event.get("result")
                content = result.get("content") if isinstance(result, dict) else None
                events.append(
                    {
                        "kind": "result",
                        "title": f"{tool_names.get(tool_call_id, 'Tool')} result",
                        "result": text_from_content(content) if content is not None else result,
                        "tool_id": tool_call_id,
                        "status": "error" if event.get("isError") else "completed",
                    }
                )
            elif event_type == "message_end" and isinstance(event.get("message"), dict):
                message = event["message"]
                if message.get("role") == "assistant":
                    text = "\n".join(
                        block["text"]
                        for block in message.get("content", [])
                        if isinstance(block, dict)
                        and block.get("type") == "text"
                        and isinstance(block.get("text"), str)
                    )
                    if text:
                        final_result = text
                        events.append({"kind": "message", "title": "Agent message", "text": text})
                    usage = message.get("usage")
                    if isinstance(usage, dict):
                        pi_messages[str(len(pi_messages))] = pi_usage_to_common(usage)
            continue

        if event_type == "system" and event.get("subtype") == "init":
            trace_metadata["model"] = event.get("model")
            trace_metadata["client_version"] = event.get("claude_code_version")
        elif event_type == "system" and event.get("subtype") == "thinking_tokens":
            estimate = event.get("estimated_tokens")
            if current_claude_message and isinstance(estimate, (int, float)):
                claude_messages.setdefault(current_claude_message, {})[
                    "_estimated_output_tokens"
                ] = estimate
        elif event_type == "stream_event" and isinstance(event.get("event"), dict):
            stream_event = event["event"]
            if stream_event.get("type") == "message_start" and isinstance(stream_event.get("message"), dict):
                message = stream_event["message"]
                message_id = str(message.get("id") or event.get("uuid") or "")
                current_claude_message = message_id
                if isinstance(message.get("usage"), dict):
                    claude_messages[message_id] = dict(message["usage"])
        elif event_type == "assistant" and isinstance(event.get("message"), dict):
            message = event["message"]
            message_id = str(message.get("id") or event.get("uuid") or "")
            current_claude_message = message_id
            if isinstance(message.get("usage"), dict):
                previous_estimate = claude_messages.get(message_id, {}).get("_estimated_output_tokens")
                claude_messages[message_id] = dict(message["usage"])
                if previous_estimate is not None:
                    claude_messages[message_id]["_estimated_output_tokens"] = previous_estimate
            for block in message.get("content", []):
                if not isinstance(block, dict):
                    continue
                block_type = block.get("type")
                if block_type == "text" and isinstance(block.get("text"), str):
                    final_result = block["text"]
                    kind = "error" if event.get("error") else "message"
                    events.append({"kind": kind, "title": "Agent message", "text": block["text"]})
                elif block_type == "tool_use":
                    tool_id = str(block.get("id") or "")
                    name = str(block.get("name") or "Tool")
                    tool_names[tool_id] = readable_tool_name(name)
                    events.append(
                        {
                            "kind": "tool",
                            "title": readable_tool_name(name),
                            "arguments": block.get("input"),
                            "tool_id": tool_id,
                            "status": "called",
                        }
                    )
        elif event_type == "user" and isinstance(event.get("message"), dict):
            for block in event["message"].get("content", []):
                if not isinstance(block, dict) or block.get("type") != "tool_result":
                    continue
                tool_id = str(block.get("tool_use_id") or "")
                events.append(
                    {
                        "kind": "result",
                        "title": f"{tool_names.get(tool_id, 'Tool')} result",
                        "result": block.get("content"),
                        "tool_id": tool_id,
                        "status": "completed",
                    }
                )
        elif event_type == "result":
            if isinstance(event.get("result"), str):
                final_result = event["result"]
            if isinstance(event.get("usage"), dict):
                trace_metadata["usage"] = event["usage"]

    if client == "claude" and not trace_metadata.get("usage") and claude_messages:
        trace_metadata["usage"] = aggregate_usage(claude_messages)
        trace_metadata["usage_is_estimate"] = True
    if client == "pi" and not trace_metadata.get("usage") and pi_messages:
        trace_metadata["usage"] = aggregate_usage(pi_messages)
        trace_metadata["usage_is_estimate"] = True

    return events, final_result, trace_metadata


def submission_history(
    events: list[dict[str, Any]], artifacts: list[dict[str, str]]
) -> list[dict[str, Any]]:
    versions: list[dict[str, Any]] = []
    for event_number, event in enumerate(events, start=1):
        arguments = event.get("arguments")
        if event.get("title") != "Write Text File" or not isinstance(arguments, dict):
            continue
        if arguments.get("path") != "submission.bf" or not isinstance(arguments.get("content"), str):
            continue
        content = arguments["content"]
        versions.append(
            {
                "version": len(versions) + 1,
                "event_number": event_number,
                "source": "Agent write",
                "content": content,
                "bytes": len(content.encode("utf-8")),
            }
        )

    final_artifact = next(
        (artifact for artifact in artifacts if artifact["path"] == "submission.bf"),
        None,
    )
    if final_artifact and (
        not versions or versions[-1]["content"] != final_artifact["content"]
    ):
        content = final_artifact["content"]
        versions.append(
            {
                "version": len(versions) + 1,
                "event_number": None,
                "source": "Final artifact",
                "content": content,
                "bytes": len(content.encode("utf-8")),
            }
        )
    return versions


def session_data(session_root: Path) -> dict[str, Any]:
    control = session_root / "control"
    workspace = session_root / "workspace"
    launch = read_json(control / "launch.json")
    result = read_json(control / "client-result.json")
    state = read_json(control / "state" / "state.json")
    manifest = read_json(control / "export-manifest.json")
    problem = read_json(workspace / "problem.json")
    client = str(launch.get("client") or manifest.get("client") or "unknown")
    events, final_result, trace_metadata = parse_trace(control / "agent-trace.jsonl", client)

    started = launch.get("started_at_epoch") or manifest.get("started_at_epoch")
    ended = state.get("ended_at_epoch") or manifest.get("ended_at_epoch")
    duration = manifest.get("duration_seconds")
    if duration is None and isinstance(started, (int, float)) and isinstance(ended, (int, float)):
        duration = round(max(0.0, ended - started), 3)

    status = state.get("end_reason") or result.get("termination_reason") or "active"
    reported_usage = result.get("reported_usage") or manifest.get("reported_usage") or {}
    usage = reported_usage.get("usage") if isinstance(reported_usage, dict) else {}
    usage_is_estimate = False
    if not isinstance(usage, dict) or not usage:
        usage = trace_metadata.get("usage", {})
        usage_is_estimate = bool(trace_metadata.get("usage_is_estimate"))

    model, model_is_inferred = resolve_model(client, launch, trace_metadata)
    api_cost = estimate_api_cost(str(model), usage)

    artifacts: list[dict[str, str]] = []
    artifact_root = workspace / "artifacts"
    if artifact_root.is_dir():
        for path in sorted(artifact_root.rglob("*")):
            if path.is_file() and not path.is_symlink():
                artifacts.append(
                    {
                        "path": str(path.relative_to(artifact_root)),
                        "content": read_text(path),
                    }
                )

    return {
        "id": session_root.name,
        "client": client,
        "client_version": launch.get("client_version") or trace_metadata.get("client_version"),
        "model": model,
        "model_is_inferred": model_is_inferred,
        "problem_id": launch.get("problem") or problem.get("id") or "Unknown",
        "problem_title": problem.get("title") or "Untitled problem",
        "problem_description": problem.get("description") or "",
        "status": status,
        "started_at_epoch": started,
        "duration_seconds": duration,
        "final_attempts_used": state.get("final_attempts_used"),
        "max_final_attempts": state.get("max_final_attempts") or launch.get("max_final_attempts"),
        "usage": usage,
        "usage_is_estimate": usage_is_estimate,
        "api_cost": api_cost,
        "result": final_result,
        "artifacts": artifacts,
        "submission_history": submission_history(events, artifacts),
        "events": events,
        "event_count": len(events),
    }


def list_sessions(sessions_root: Path) -> list[dict[str, Any]]:
    if not sessions_root.is_dir():
        return []
    sessions = []
    for path in sorted(sessions_root.iterdir(), reverse=True):
        if path.is_dir() and (path / "control").is_dir():
            sessions.append(session_data(path))
    return sessions


def make_handler(sessions_root: Path) -> type[BaseHTTPRequestHandler]:
    class ViewerHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            route = urlparse(self.path).path
            if route == "/api/sessions":
                self.send_json({"sessions": list_sessions(sessions_root)})
                return
            if route == "/":
                route = "/index.html"
            relative = Path(unquote(route.lstrip("/")))
            static_path = (STATIC_ROOT / relative).resolve()
            try:
                static_path.relative_to(STATIC_ROOT.resolve())
            except ValueError:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            if not static_path.is_file():
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            content = static_path.read_bytes()
            content_type = mimetypes.guess_type(static_path.name)[0] or "application/octet-stream"
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", f"{content_type}; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def send_json(self, value: Any) -> None:
            content = json.dumps(value, ensure_ascii=False).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def log_message(self, format: str, *args: Any) -> None:
            return

    return ViewerHandler


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Show evaluation session results and traces.")
    parser.add_argument("--sessions-root", type=Path, default=PROJECT_ROOT / ".sessions")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    return parser.parse_args()


def main() -> int:
    arguments = parse_args()
    server = ThreadingHTTPServer(
        (arguments.host, arguments.port),
        make_handler(arguments.sessions_root.resolve()),
    )
    print(f"Session viewer: http://{arguments.host}:{arguments.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
