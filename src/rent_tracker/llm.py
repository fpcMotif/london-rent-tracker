import json
import subprocess


class LlmError(Exception):
    pass


def _failure(out: subprocess.CompletedProcess) -> LlmError:
    try:
        reply = json.loads(out.stdout)
    except json.JSONDecodeError:
        return LlmError(
            f"exit {out.returncode}: {(out.stderr or out.stdout).strip()[:300]}"
        )
    parts = [reply.get("subtype"), reply.get("terminal_reason"), reply.get("result")]
    parts += [str(e) for e in reply.get("errors") or []]
    return LlmError(
        f"exit {out.returncode}: " + " | ".join(str(p)[:300] for p in parts if p)
    )


def structured(
    model: str, instruction: str, content: str, schema: dict, timeout: int = 300
) -> dict:
    cmd = [
        "claude",
        "-p",
        "--model",
        model,
        "--output-format",
        "json",
        "--strict-mcp-config",
        "--setting-sources",
        "",
        "--tools",
        "",
        "--no-session-persistence",
        "--json-schema",
        json.dumps(schema),
        instruction,
    ]
    out = subprocess.run(
        cmd, input=content, capture_output=True, text=True, timeout=timeout, check=False
    )
    if out.returncode != 0:
        raise _failure(out)
    reply = json.loads(out.stdout)
    if reply.get("is_error") or "structured_output" not in reply:
        raise _failure(out)
    return reply["structured_output"]


def write_with_search(
    model: str, instruction: str, content: str, timeout: int = 900
) -> str:
    cmd = [
        "claude",
        "-p",
        "--model",
        model,
        "--output-format",
        "json",
        "--strict-mcp-config",
        "--setting-sources",
        "",
        "--tools",
        "WebSearch,WebFetch",
        "--allowedTools",
        "WebSearch,WebFetch",
        "--no-session-persistence",
        instruction,
    ]
    out = subprocess.run(
        cmd, input=content, capture_output=True, text=True, timeout=timeout, check=False
    )
    if out.returncode != 0:
        raise _failure(out)
    reply = json.loads(out.stdout)
    if reply.get("is_error"):
        raise _failure(out)
    return reply["result"]
