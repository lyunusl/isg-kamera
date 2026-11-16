"""Developer tool: rebuilds the ready-made scene that ships with the program (installer/scene/isg-kamera.pmod).

Studio saves scene files encrypted, and saving is only possible with File → Save As in the Studio window.
So the scene is built once, in an open Studio on the developer's computer:

    1. Open AugeLab Studio (MCP must be available at the default 127.0.0.1:8765).
    2. python installer/build_scene.py    → registers the ISG blocks with Studio and builds the scene in a new tab
    3. In Studio, save that tab with File → Save As as installer/scene/isg-kamera.pmod.

The scene binds the blocks by opCode and socket name and connects them by socket index. When the ISG block definitions
(studio_blocks.py) change only in their code or description, the scene does not need to be rebuilt: the panel updates the
block files on every run. Rebuild it when an opCode, a title, a socket name or the number or order of the inputs/outputs
changes. This tool does not run on the user's computer; there the scene runs without opening the Studio window (studio.py).
"""
from __future__ import annotations

import asyncio
import json
import secrets
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import studio_blocks  # noqa: E402

MCP_URL = "http://127.0.0.1:8765/mcp"
STRING_INPUT_OP = "119"  # String Input
CODE_TITLE = "ISG Camera Code"
SCENE_CODE = "ISG"  # ignored in a headless run: the camera comes from an environment variable (isg_core.blocks.ENV_CAMERA)


def _idempotency_key() -> str:
    return "isg-" + secrets.token_hex(12)


async def call_tool(tool: str, args: dict[str, Any]) -> dict[str, Any]:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    async with streamable_http_client(MCP_URL) as (read, write, *_):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool, args)
    payload = getattr(result, "structuredContent", None) or getattr(result, "structured_content", None) or {}
    if not payload and result.content:
        try:
            payload = json.loads(getattr(result.content[0], "text", "") or "{}")
        except ValueError:
            payload = {"error": getattr(result.content[0], "text", "")}
    if getattr(result, "isError", False) or getattr(result, "is_error", False) or payload.get("error"):
        raise SystemExit(f"{tool}: {json.dumps(payload.get('error') or payload, ensure_ascii=False)[:500]}")
    data = dict(payload.get("data") or {})
    if payload.get("operation") and "operation" not in data:
        data["operation"] = payload["operation"]
    return data


async def wait_operation(data: dict[str, Any], timeout_s: float) -> dict[str, Any]:
    op = data.get("operation") or data
    start = time.monotonic()
    while op.get("state") in ("queued", "running") and time.monotonic() - start < timeout_s:
        more = await call_tool("operation.get", {"operationId": op["id"], "waitTimeoutMs": 15000, "updateLimit": 1})
        op = more.get("operation") or more
    return op


def scene_source() -> str:
    b = studio_blocks
    return "\n".join([
        f"code = node('{STRING_INPUT_OP}', title='{CODE_TITLE}')",
        f"source = node('{b.VIDEO_OP}', title='{b.VIDEO_TITLE}')",
        f"detect = node('{b.DETECT_OP}', title='{b.DETECT_TITLE}')",
        f"pose = node('{b.POSE_OP}', title='{b.POSE_TITLE}')",
        f"rules = node('{b.RULES_OP}', title='{b.RULES_TITLE}')",
        f"code.params['threshold'] = '{SCENE_CODE}'",
        "connect(code.out[0], source.in_[0])",
        "connect(source.out[0], detect.in_[0])",
        "connect(code.out[0], detect.in_[1])",
        "connect(source.out[0], rules.in_[0])",
        "connect(source.out[0], pose.in_[0])",
        "connect(detect.out[0], pose.in_[1])",
        "connect(pose.out[0], rules.in_[1])",
        "connect(code.out[0], rules.in_[2])",
        "connect(source.out[1], rules.in_[3])",
        "connect(source.out[2], rules.in_[4])",
    ]) + "\n"


async def main() -> None:
    op = await wait_operation(await call_tool("node.custom.create", {"specifications": studio_blocks.DEFINITIONS, "replaceExisting": True,
                                                                     "idempotencyKey": _idempotency_key()}), 120)
    if op.get("state") != "succeeded":
        raise SystemExit(f"Could not add the ISG blocks to Studio: {op.get('error') or op.get('state')}")
    data = await call_tool("graph.program", {"target": "create_new", "expectedRevision": "none", "idempotencyKey": _idempotency_key(),
                                             "source": scene_source()})
    target = Path(__file__).resolve().parent / "scene" / "isg-kamera.pmod"
    print(f"Scene built in Studio (scene {data.get('scene')}). In Studio, save this tab with File → Save As to:\n  {target}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main())
