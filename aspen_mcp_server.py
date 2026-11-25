#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""DSH Aspen 插件 —— MCP stdio 服务器。

协议：JSON-RPC 2.0 / 换行分隔（MCP stdio transport），纯同步实现，
不依赖 mcp SDK，也不用 asyncio，避免事件循环在受限环境里卡住握手。

工具（8 个）：
  aspen_status       服务器/安装/许可/会话状态
  aspen_open_case    打开 .bkp/.inp（可选立即运行）
  aspen_run          运行当前案例（Run2）
  aspen_get          读取一个或多个树节点值（\Data\...）
  aspen_set          写入 Input 节点值
  aspen_list         列出某个节点的子节点（默认 \Data\Streams）
  aspen_save         保存案例（默认存回原文件）
  aspen_close        关闭案例
"""
from __future__ import annotations

import json
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from aspen_engine import AspenEngine, AspenError  # noqa: E402

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "aspen"
SERVER_VERSION = "0.1.0"

ENGINE = AspenEngine()


def log(message: str) -> None:
    try:
        sys.stderr.write("[aspen-mcp] %s\n" % message)
        sys.stderr.flush()
    except Exception:
        pass


def _text_result(payload: dict, is_error: bool = False) -> dict:
    return {
        "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=1)}],
        "isError": is_error,
    }


TOOLS = [
    {
        "name": "aspen_status",
        "description": "查看 Aspen 插件状态：是否已打开案例、Aspen 安装路径、pywin32 是否可用、最近一次运行结果。首次使用或报错后先调用它。",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "aspen_open_case",
        "description": "打开一个 Aspen Plus 案例文件（.bkp / .inp，绝对路径），返回流股与模块列表；run=true 时立即运行。打开新案例会关闭当前案例。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "案例文件绝对路径，例如 D:\\work\\case.bkp"},
                "run": {"type": "boolean", "description": "打开后是否立即运行（默认 false）", "default": False},
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    },
    {
        "name": "aspen_run",
        "description": "运行当前案例（Engine.Run2），返回耗时与 PER_ERROR（0 表示收敛）。运行前务必确认输入完整。",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "aspen_get",
        "description": "按 Aspen 树路径读取节点值。paths 可传一个字符串或字符串数组，例如 \\Data\\Streams\\1\\Output\\RES_TEMP 或 \\Data\\Results Summary\\Run-Status\\Output\\PER_ERROR。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "paths": {
                    "description": "树路径：字符串或字符串数组",
                    "anyOf": [{"type": "string"}, {"type": "array", "items": {"type": "string"}}],
                }
            },
            "required": ["paths"],
            "additionalProperties": False,
        },
    },
    {
        "name": "aspen_set",
        "description": "写入一个 Input 节点的值（例如进料温度 \\Data\\Streams\\1\\Input\\TEMP）或直接把一条树路径批量写入。只改内存中的案例，需 aspen_save 才会落盘。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "树路径"},
                "value": {"description": "新值（数字、字符串、布尔）"},
            },
            "required": ["path", "value"],
            "additionalProperties": False,
        },
    },
    {
        "name": "aspen_list",
        "description": "列出某个树节点的子节点名称（with_values=true 时同时返回值）。默认列流股 \\Data\\Streams；模块用 \\Data\\Blocks；某条流股的输出用 \\Data\\Streams\\1\\Output。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "树路径，默认 \\Data\\Streams"},
                "with_values": {"type": "boolean", "description": "是否同时返回子节点的值", "default": False},
                "limit": {"type": "integer", "description": "最多返回多少个子节点，默认 300", "default": 300},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "aspen_save",
        "description": "保存当前案例。不带 path 时保存回打开时的文件；带 path 时尝试另存为新文件（部分版本不支持 SaveAs）。",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "另存为的绝对路径（可选）"}},
            "additionalProperties": False,
        },
    },
    {
        "name": "aspen_close",
        "description": "关闭当前案例，释放 Aspen 会话。save=true 会先保存。",
        "inputSchema": {
            "type": "object",
            "properties": {"save": {"type": "boolean", "description": "关闭前是否保存", "default": False}},
            "additionalProperties": False,
        },
    },
]

HANDLERS = {
    "aspen_status": lambda args: ENGINE.status(),
    "aspen_open_case": lambda args: ENGINE.open_case(args["path"], bool(args.get("run", False))),
    "aspen_run": lambda args: ENGINE.run(),
    "aspen_get": lambda args: ENGINE.get(args["paths"]),
    "aspen_set": lambda args: ENGINE.set(args["path"], args["value"]),
    "aspen_list": lambda args: ENGINE.list_children(
        args.get("path") or r"\Data\Streams",
        bool(args.get("with_values", False)),
        int(args.get("limit", 300)),
    ),
    "aspen_save": lambda args: ENGINE.save(args.get("path")),
    "aspen_close": lambda args: ENGINE.close(bool(args.get("save", False))),
}


def dispatch(method: str, params: dict):
    if method == "initialize":
        return {
            "protocolVersion": params.get("protocolVersion") or PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        }
    if method == "tools/list":
        return {"tools": TOOLS}
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        handler = HANDLERS.get(name)
        if handler is None:
            return _text_result({"error_code": "unknown_tool", "message": "未知工具：%s" % name}, True)
        try:
            return _text_result({"ok": True, "result": handler(args)})
        except AspenError as exc:
            ENGINE.last_error = exc.to_dict()
            return _text_result({"ok": False, **exc.to_dict()}, True)
        except Exception as exc:  # 兜底：把 traceback 打到 stderr，返回结构化错误
            tb = traceback.format_exc()
            log("tool %s crashed:\n%s" % (name, tb))
            return _text_result({"ok": False, "error_code": "internal_error", "message": str(exc)}, True)
    if method in ("ping", "logging/setLevel"):
        return {}
    if method.startswith("notifications/"):
        return None
    raise KeyError(method)


def respond(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main() -> None:
    log("started pid=%s python=%s" % (os.getpid(), sys.version.split()[0]))
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except Exception:
            respond({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}})
            continue
        request_id = request.get("id")
        method = request.get("method") or ""
        params = request.get("params") or {}
        try:
            result = dispatch(method, params)
        except KeyError:
            if request_id is not None:
                respond({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "method not found: %s" % method}})
            continue
        except Exception as exc:
            log("dispatch error: %s" % traceback.format_exc())
            if request_id is not None:
                respond({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32603, "message": str(exc)}})
            continue
        if request_id is None:
            continue
        respond({"jsonrpc": "2.0", "id": request_id, "result": result})
    log("stdin closed, exiting")


if __name__ == "__main__":
    main()
