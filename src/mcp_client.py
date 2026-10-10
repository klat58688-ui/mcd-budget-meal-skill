# -*- coding: utf-8 -*-
"""
麦当劳 MCP Server 轻量客户端（Streamable HTTP）

- 协议：Streamable HTTP（POST + SSE 响应）
- 认证：请求头 Authorization: Bearer <MCP_TOKEN>
- Token 从环境变量 MCP_TOKEN 读取，绝不硬编码

依赖：httpx。如环境装有官方 `mcp` SDK，demo.py 优先使用 SDK；
本模块作为零依赖兜底实现，同时便于理解协议细节。
"""

from __future__ import annotations

import json
import os
import uuid

import httpx

MCP_URL = os.environ.get("MCP_URL", "https://mcp.mcd.cn")


class McpError(RuntimeError):
    """MCP 调用异常（协议错误 / 业务错误统一抛出）"""


class McdMcpClient:
    """最小可用的 Streamable HTTP MCP 客户端"""

    def __init__(self, token: str | None = None, url: str | None = None,
                 timeout: float = 30.0):
        self.token = token or os.environ.get("MCP_TOKEN", "")
        if not self.token:
            raise McpError("未找到 MCP_TOKEN 环境变量，请先配置 .env 或导出环境变量")
        self.url = url or MCP_URL
        self.timeout = timeout
        self.session_id: str | None = None
        self._client = httpx.Client(timeout=self.timeout)

    # ---------- 协议层 ----------

    def _headers(self) -> dict:
        h = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            # Streamable HTTP 要求同时接受 JSON 与 SSE
            "Accept": "application/json, text/event-stream",
        }
        if self.session_id:
            h["Mcp-Session-Id"] = self.session_id
        return h

    def _send(self, payload: dict) -> dict:
        """发送一条 JSON-RPC 消息并解析响应（兼容 application/json 与 SSE）。
        SSE 用流式逐行解析，匹配到本请求的响应即返回，避免大菜单一性次读入内存。"""
        req_id = payload.get("id")
        with self._client.stream("POST", self.url, json=payload,
                                 headers=self._headers()) as resp:
            sid = resp.headers.get("Mcp-Session-Id")
            if sid:
                self.session_id = sid
            if resp.status_code == 202:  # 通知类消息，无响应体
                return {}
            if resp.status_code in (401, 403):
                raise McpError(
                    f"鉴权失败（HTTP {resp.status_code}）：MCP_TOKEN 无效或已过期，"
                    "请到 https://open.mcd.cn/mcp 重新申请并更新 .env / 环境变量")
            if resp.status_code >= 400:
                raise McpError(f"HTTP {resp.status_code}: {resp.read().decode('utf-8', 'replace')[:300]}")

            ctype = resp.headers.get("Content-Type", "")
            if "text/event-stream" in ctype:
                # SSE：服务端可能先推 notification 再推 response，按请求 id 精确匹配
                fallback = {}
                for line in resp.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if not data:
                        continue
                    msg = json.loads(data)
                    if req_id is not None and msg.get("id") == req_id:
                        return msg
                    if "id" in msg:  # 其他响应，留作兜底
                        fallback = msg
                return fallback
            return resp.json()

    def _rpc(self, method: str, params: dict | None = None) -> dict:
        req_id = uuid.uuid4().hex
        payload = {"jsonrpc": "2.0", "id": req_id, "method": method}
        if params is not None:
            payload["params"] = params
        data = self._send(payload)
        if "error" in data:
            raise McpError(f"{method} 失败: {data['error']}")
        return data.get("result", {})

    # ---------- MCP 标准方法 ----------

    def initialize(self) -> dict:
        """初始化会话（必须先于其他调用）"""
        result = self._rpc("initialize", {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "mcd-budget-meal-skill", "version": "1.0.0"},
        })
        # 按协议发送 initialized 通知
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return result

    def list_tools(self) -> list[dict]:
        """获取服务端当前开放的全部工具（以实际返回为准）"""
        result = self._rpc("tools/list")
        return result.get("tools", [])

    def call_tool(self, name: str, arguments: dict | None = None) -> dict:
        """调用指定工具，返回结构化结果"""
        result = self._rpc("tools/call",
                           {"name": name, "arguments": arguments or {}})
        # content 为 MCP 标准内容块；structuredContent 为结构化数据（如有）
        if result.get("structuredContent") is not None:
            return result["structuredContent"]
        # 兜底：把 text 内容块尝试解析为 JSON
        for block in result.get("content", []):
            if block.get("type") == "text":
                try:
                    return json.loads(block["text"])
                except (json.JSONDecodeError, KeyError):
                    return {"text": block["text"]}
        return result

    def close(self):
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
