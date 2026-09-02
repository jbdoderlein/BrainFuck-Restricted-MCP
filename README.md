# Restricted Brainfuck Evaluation Harness

This harness lets Codex CLI or Claude Code solve Brainfuck problems.
It removes their general-purpose execution tools.

The model can use five MCP tools:

- `write_text_file` writes arbitrary UTF-8 text.
- `run_brainfuck` runs `submission.bf` with one exact input.
- `evaluate_solution` runs public and semi-private tests.
- `submit_solution` uses one final attempt.
- `get_budget_status` reports the remaining time and final attempts.

The model can write source code as text.
The model cannot execute that text as a system program.

## Requirements

- Linux
- Python 3.11 or later
- Bubblewrap
- The `tritium` executable in the project root
- Codex CLI 0.152.1 or later, or Claude Code
- A valid subscription login for the selected client

The launcher uses your existing client login.
It does not require an OpenAI or Anthropic API key.

## Start an evaluation

Run Codex:

```sh
./scripts/run-codex E02
```

Run Claude Code:

```sh
./scripts/run-claude E02
```

Add `--model MODEL` to select a model.
For Claude Code, add `--effort LEVEL` to set the reasoning effort.
Valid levels are `low`, `medium`, `high`, `xhigh`, and `max`.
Add `--dry-run` to prepare a session without contacting the model service.

For example:

```sh
./scripts/run-claude E02 --model sonnet --effort high
```

The default session has three final attempts and a 30-minute time limit.
Use these options to change the limits:

```sh
./scripts/run-codex E02 --final-attempts 5 --time-limit-minutes 60
```

Each run creates a private directory under `.sessions/`.
The agent works only with the public problem and its artifact directory.
The launch record contains the client version and file hashes.

The launcher uses an owner-only link to the selected client credential file.
It removes this link when the client exits.
Dry runs do not create credential links.

## Session budget

The launcher stops the client when the wall-time budget expires.
All MCP tools reject new work after the deadline.

The agent can call `get_budget_status` at any time.
The result contains the elapsed time, remaining time, and final attempts.

The subscription clients do not supply a portable session token limit.
Codex reports token use after a turn in its JSON event output.
Claude Code reports available usage data in its stream output.
The session export preserves this reported usage.
Claude exports also contain all partial-message events that Claude Code provides.
These events do not guarantee access to hidden model reasoning.

Codex documents its JSON usage events in the
[non-interactive mode guide](https://developers.openai.com/codex/noninteractive/).
Claude Code documents `--max-budget-usd` for API calls in its
[CLI reference](https://code.claude.com/docs/en/cli-reference).

## Session export

The launcher creates a ZIP file when the client exits.
It also creates the ZIP file after a final result or a time-limit stop.
The launcher prints the ZIP path.

The ZIP file contains these records:

- The agent event trace.
- The client error log.
- The public problem and agent prompt.
- All agent artifacts.
- The MCP tool audit.
- The final state, result, timing, model, effort, and reported token usage.

The ZIP file does not contain credentials, generated client configuration,
or private test files.

## Add problems

Copy `data/problems.example.json` to a private problem catalog.
Keep only descriptions and public tests in this catalog.

Store semi-private tests at this path:

```text
TEST_ROOT/semi_private/PROBLEM_ID.json
```

Store private tests at this path:

```text
TEST_ROOT/private/PROBLEM_ID.json
```

Start the client with the private paths:

```sh
./scripts/run-codex E02 \
  --catalog /absolute/path/problems.json \
  --tests-root /absolute/path/tests
```

Do not put the private test directory inside an agent workspace.
Restrict its operating-system permissions to the harness owner.

## Test behavior

`evaluate_solution` runs the public tests first.
It then runs the semi-private tests.
It returns the first failed test with its input and output.

Each `submit_solution` call uses one final attempt.
It runs the public and semi-private tests first.
It then runs the private tests.

The default final-attempt limit is three.
A failed attempt does not lock the session when another attempt remains.
A successful attempt or the last attempt ends the session.

A private failure returns only this message:

```text
A hidden test failed.
```

The model cannot submit again after the session ends.

## Execution boundary

The gateway invokes one fixed executable with fixed arguments.
It forces Tritium interpreter mode with 8-bit cells and binary input.
EOF produces zero.
Negative tape positions produce an error.

Bubblewrap gives each interpreter process a new network namespace.
The process receives a read-only interpreter and submission file.
The process receives no test files and no artifact directory.

The gateway also limits CPU time, memory, output size, and open files.
It fails closed when Bubblewrap is unavailable.

Codex uses a dedicated configuration directory for each run.
Its shell, web, browser, plugin, skill, image, and subagent features are disabled.
The `mcp__brainfuck` namespace uses direct MCP calls.
The Code Mode execution host remains disabled.

Claude Code uses restricted mode and an empty built-in tool list.
It loads only the generated MCP configuration.

A client exit without a final result has the `incomplete_client_exit` reason.
The launcher returns exit code 3 when the client itself returned exit code 0.

## Development tests

Run:

```sh
python3 -m unittest discover -s tests -v
```

The files under `data/tests.example/` are demonstration data.
They are not secret tests.
