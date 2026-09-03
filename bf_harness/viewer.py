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


def parse_trace(path: Path, client: str) -> tuple[list[dict[str, Any]], str, dict[str, Any]]:
    events: list[dict[str, Any]] = []
    final_result = ""
    trace_metadata: dict[str, Any] = {}
    tool_names: dict[str, str] = {}

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

        if event_type == "system" and event.get("subtype") == "init":
            trace_metadata["model"] = event.get("model")
            trace_metadata["client_version"] = event.get("claude_code_version")
        elif event_type == "assistant" and isinstance(event.get("message"), dict):
            message = event["message"]
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

    return events, final_result, trace_metadata


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
    if not isinstance(usage, dict) or not usage:
        usage = trace_metadata.get("usage", {})

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
        "model": launch.get("model") or trace_metadata.get("model") or "Default",
        "problem_id": launch.get("problem") or problem.get("id") or "Unknown",
        "problem_title": problem.get("title") or "Untitled problem",
        "problem_description": problem.get("description") or "",
        "status": status,
        "started_at_epoch": started,
        "duration_seconds": duration,
        "final_attempts_used": state.get("final_attempts_used"),
        "max_final_attempts": state.get("max_final_attempts") or launch.get("max_final_attempts"),
        "usage": usage,
        "result": final_result,
        "artifacts": artifacts,
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
