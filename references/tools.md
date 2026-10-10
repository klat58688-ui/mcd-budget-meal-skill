# MCP 工具清单与调用示例

> 连接方式：Streamable HTTP，`POST https://mcp.mcd.cn`
> 认证头：`Authorization: Bearer ${MCP_TOKEN}`
> ⚠️ 以下为逻辑工具名与典型参数，**一律以实际 `tools/list` 返回为准**。

## 工具清单

| 逻辑名称 | 用途 | 关键入参 | 关键返回 |
|---|---|---|---|
| 一键领券 | 领取当前全部可领优惠券 | `claimAll: true` | 领取结果列表 |
| 券列表查询 | 查询已持有可用券 | （无） | 券 id、面额、门槛、适用品 |
| 门店菜单查询 | 按城市/门店查实时菜单与价格 | `city` / `storeId` | 商品 id、名称、价格、分类、在售状态 |
| 点餐下单 | 提交订单（自取/麦乐送） | `orderType`、`storeId`、`items[]`、`couponIds[]` | 订单号、金额、取餐信息 |
| 活动日历 | 查询近期营销活动 | `city`（可选） | 活动列表、时间 |
| 营养查询 | 查询餐品营养成分 | `itemId` | 热量、蛋白质等 |
| 积分兑换 | 查询/兑换积分权益 | `points`、`rewardId` | 兑换结果 |

## 调用示例

### 1. list_tools

```json
// 请求
{"jsonrpc": "2.0", "id": "1", "method": "tools/list"}
// 响应（节选）
{"jsonrpc": "2.0", "id": "1", "result": {"tools": [
  {"name": "claim_coupons", "description": "一键领取全部可领优惠券", "inputSchema": {...}},
  {"name": "get_store_menu", "description": "查询门店实时菜单", "inputSchema": {...}}
]}}
```

### 2. 一键领券

```json
{"jsonrpc": "2.0", "id": "2", "method": "tools/call",
 "params": {"name": "claim_coupons", "arguments": {"claimAll": true}}}
```

### 3. 菜单查询

```json
{"jsonrpc": "2.0", "id": "3", "method": "tools/call",
 "params": {"name": "get_store_menu", "arguments": {"city": "广州"}}}
```

返回归一化示例：

```json
{"items": [
  {"id": "b3", "name": "麦香鸡", "price": 14.0, "category": "staple", "available": true},
  {"id": "s1", "name": "小薯条", "price": 9.0, "category": "snack", "available": true}
]}
```

### 4. 下单

```json
{"jsonrpc": "2.0", "id": "4", "method": "tools/call",
 "params": {"name": "create_order", "arguments": {
   "orderType": "pickup",
   "storeId": "GZ001",
   "items": [{"id": "b3", "qty": 2}, {"id": "s1", "qty": 1}],
   "couponIds": ["c1"]
 }}}
```

## 会话管理（Mcp-Session-Id）

Streamable HTTP 是有会话的，照抄请求不带头会 400，注意三点：

1. **initialize 拿会话**：第一次调用 `initialize` 后，服务端在**响应头**返回 `Mcp-Session-Id`，必须保存。
2. **后续请求带会话**：之后每个请求（包括 `tools/list`、`tools/call`）都要在请求头带上 `Mcp-Session-Id: <值>`。
3. **initialized 通知**：`initialize` 成功后按协议发一条 `notifications/initialized`（无 id 的 JSON-RPC 通知，服务端回 202 无响应体），否则部分实现会拒绝后续调用。

```http
POST / HTTP/1.1
Host: mcp.mcd.cn
Authorization: Bearer ${MCP_TOKEN}
Content-Type: application/json
Accept: application/json, text/event-stream
Mcp-Session-Id: 3fa85f64-5717-4562-b3fc-2c963f66afa6
```

> 响应可能是 `application/json`，也可能是 `text/event-stream`（SSE）。SSE 时逐行读 `data:` 前缀的 JSON-RPC 消息，**按请求 id 匹配响应**（流中可能夹带服务端 notification），匹配到即结束。`src/mcp_client.py` 已实现以上全部细节。

## 错误处理

| HTTP / 业务码 | 含义 | 处理建议 |
|---|---|---|
| 401 | Token 无效或过期 | 重新申请 MCP_TOKEN |
| 429 | 调用频率超限 | 退避重试，合并调用 |
| 券不可用 | 券过期/门槛不足/适用品不含 | 重拉券列表后重凑单 |
| 商品售罄 | 下单时缺货 | 剔除后重凑，向用户说明 |
