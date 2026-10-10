# -*- coding: utf-8 -*-
"""
演示脚本：把「20 元双人餐」完整流程跑通

流程：list_tools → 领券 → 查菜单 → 凑单 → （可选）下单 → 战报

运行：
    cp .env.example .env   # 填入 MCP_TOKEN
    python demo.py

说明：
- 工具名以 list_tools 实际返回为准；下方 TOOL_NAMES 为常见命名的映射表，
  脚本会先按映射匹配，匹配不到则按关键词模糊查找。
- 未设置 AUTO_ORDER=1 时不会真实下单，只打印将要提交的订单。
"""

from __future__ import annotations

import os
import sys

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from src.budget_engine import (  # noqa: E402
    Coupon, MenuItem, generate_plans, render_battle_report,
)
from src.mcp_client import McdMcpClient, McpError  # noqa: E402

load_dotenv()

# 常见工具名映射（以 list_tools 实际返回为准，此处为兜底关键词）
# 注意顺序：券列表优先匹配 list/get/查询 类命名，避免误命中"一键领券"
TOOL_KEYWORDS = {
    "claim_coupons": ["claim", "领取", "coupon_claim", "receive"],
    "list_coupons": ["list_coupon", "get_coupon", "coupon_list", "查询券", "我的券", "券列表"],
    "menu": ["menu", "菜单"],
    "order": ["order", "下单"],
    "nutrition": ["nutrition", "营养"],
    "calendar": ["calendar", "活动", "activity"],
    "points": ["points", "积分"],
}

CITY = os.environ.get("DEMO_CITY", "广州")
STORE_ID = os.environ.get("DEMO_STORE_ID", "")  # 留空则用城市默认门店
BUDGET = float(os.environ.get("DEMO_BUDGET", "20"))
HEADCOUNT = max(1, int(os.environ.get("DEMO_HEADCOUNT", "2")))  # 人数下限 1，防脏数据
NO_SPICY = os.environ.get("DEMO_NO_SPICY", "0") == "1"
AUTO_ORDER = os.environ.get("AUTO_ORDER", "0") == "1"
ORDER_TYPE = os.environ.get("ORDER_TYPE", "pickup")  # pickup自取 / delivery麦乐送
DELIVERY_FEE = float(os.environ.get("DELIVERY_FEE", "0"))  # 外送配送费（计入预算口径）


def find_tool(tools: list[dict], kind: str) -> str | None:
    """两轮匹配定位工具：第一轮只匹配工具 name（精确优先），
    第二轮才用 description 兜底，避免 list_coupons 误命中 claim_coupons 这类撞词。"""
    keywords = TOOL_KEYWORDS[kind]
    for t in tools:  # 第一轮：name 匹配
        name = t.get("name", "").lower()
        for kw in keywords:
            if kw.lower() in name:
                return t["name"]
    for t in tools:  # 第二轮：description 兜底
        desc = (t.get("description") or "")
        for kw in keywords:
            if kw in desc:
                return t["name"]
    return None


def parse_menu(raw) -> list[MenuItem]:
    """把 MCP 返回的菜单 JSON 归一化为 MenuItem 列表。
    入参统一为 dict（兼容 items/menu 两种键）；取不到 id 的商品直接跳过，防脏数据。"""
    if not isinstance(raw, dict):
        return []
    items_raw = raw.get("items") or raw.get("menu") or []
    items: list[MenuItem] = []
    for it in items_raw:
        if not isinstance(it, dict):
            continue
        item_id = it.get("id") or it.get("itemId") or it.get("sku")
        if item_id is None:
            continue  # 无 id 的商品无法下单，直接跳过
        try:
            items.append(MenuItem(
                item_id=str(item_id),
                name=it.get("name", "未知餐品"),
                price=float(it.get("price", 0)),
                category=it.get("category", "other"),
                is_veg=bool(it.get("veg") or it.get("isVeg") or it.get("vegetarian")),
                is_spicy=bool(it.get("spicy") or it.get("isSpicy") or "辣" in it.get("name", "")),
                satiety=float(it.get("satiety", _guess_satiety(it.get("category", "")))),
                available=bool(it.get("available", True) and not it.get("soldOut", False)),
            ))
        except (TypeError, ValueError):
            continue
    return items


def _guess_satiety(category: str) -> float:
    return {"staple": 3.0, "burger": 3.0, "snack": 2.0,
            "dessert": 1.0, "drink": 0.5}.get(str(category).lower(), 1.0)


def parse_coupons(raw) -> list[Coupon]:
    """归一化已领取的券列表。入参统一为 dict；取不到 id 的券直接跳过。"""
    if not isinstance(raw, dict):
        return []
    raw_list = raw.get("coupons") or []
    coupons: list[Coupon] = []
    for c in raw_list:
        if not isinstance(c, dict):
            continue
        coupon_id = c.get("id") or c.get("couponId") or c.get("code")
        if coupon_id is None:
            continue  # 无 id 的券无法用于下单，直接跳过
        try:
            coupons.append(Coupon(
                coupon_id=str(coupon_id),
                title=c.get("title") or c.get("name", "优惠券"),
                discount=float(c.get("discount") or c.get("amount") or 0),
                min_spend=float(c.get("minSpend") or c.get("threshold") or 0),
                applicable_ids=[str(x) for x in (c.get("applicableIds") or [])],
            ))
        except (TypeError, ValueError):
            continue
    return coupons


def main() -> int:
    budget, headcount = BUDGET, HEADCOUNT
    print(f"== 麦门穷鬼套餐生成器 Demo ==  预算 ¥{budget:.0f} / {headcount} 人 / 城市 {CITY}\n")

    try:
        with McdMcpClient() as client:
            client.initialize()
            tools = client.list_tools()
            print(f"[1/5] list_tools 返回 {len(tools)} 个工具："
                  f"{', '.join(t['name'] for t in tools)}\n")

            # ---- 2. 自动领券 ----
            t_claim = find_tool(tools, "claim_coupons")
            t_list = find_tool(tools, "list_coupons")
            if t_claim:
                client.call_tool(t_claim, {"claimAll": True})
                print("[2/5] 已自动领取全部可领优惠券")
            if t_list:
                coupons_raw = client.call_tool(t_list, {})
            else:
                coupons_raw = {}
                print("[2/5] 未找到券列表工具，按无券凑单")
            coupons = parse_coupons(coupons_raw)
            print(f"      当前可用券 {len(coupons)} 张："
                  f"{', '.join(c.title for c in coupons) or '无'}\n")

            # ---- 3. 查菜单 ----
            t_menu = find_tool(tools, "menu")
            if not t_menu:
                print("[3/5] 未找到菜单工具，终止。请检查 list_tools 输出并更新 TOOL_KEYWORDS。")
                return 1
            args = {"city": CITY}
            if STORE_ID:
                args["storeId"] = STORE_ID
            menu_raw = client.call_tool(t_menu, args)
            menu = parse_menu(menu_raw)
            print(f"[3/5] 菜单获取成功，在售餐品 {len(menu)} 个\n")

            # ---- 4. 凑单 ----
            result = generate_plans(menu, coupons, budget=budget,
                                    headcount=headcount, no_spicy=NO_SPICY,
                                    delivery_fee=DELIVERY_FEE if ORDER_TYPE == "delivery" else 0.0)
            if result["over_budget"]:
                p = result["cheapest"][0]
                print(f"[4/5] 预算无解：最接近方案 ¥{p.final_price:.2f}（{p.note}）\n")
                best = p
            else:
                best = result["cheapest"][0]
                print("[4/5] 凑单完成，Top3（按最便宜）：")
                for i, p in enumerate(result["cheapest"], 1):
                    detail = " + ".join(f"{x.item.name}×{x.qty}" for x in p.items)
                    print(f"  方案{i}: ¥{p.final_price:.2f}（原价 ¥{p.original_price:.2f}，"
                          f"省 ¥{p.saved:.2f}） {detail}")
                print("\n  吃得最饱 Top3：")
                for i, p in enumerate(result["fullest"], 1):
                    print(f"  方案{i}: 饱腹感 {p.total_satiety}，¥{p.final_price:.2f}")
                print()

            # ---- 5. 下单 + 战报 ----
            if AUTO_ORDER and not result["over_budget"]:
                t_order = find_tool(tools, "order")
                if t_order:
                    order_args = {
                        "orderType": ORDER_TYPE,
                        "storeId": STORE_ID or None,
                        "city": CITY,
                        "items": [{"id": x.item.item_id, "qty": x.qty} for x in best.items],
                        "couponIds": [c.coupon_id for c in best.coupons_used],
                    }
                    resp = client.call_tool(t_order, order_args)
                    print(f"[5/5] 下单成功：{resp}\n")
                else:
                    print("[5/5] 未找到下单工具，跳过下单\n")
            else:
                print(f"[5/5] 演示模式未下单（设置 AUTO_ORDER=1 开启真实下单，方式 {ORDER_TYPE}）\n")

            print("---- 省钱战报 ----")
            print(render_battle_report(best, headcount))
            return 0
    except McpError as e:
        print(f"MCP 调用失败：{e}")
        return 2
    except Exception as e:  # 兜底：网络中断、JSON 解析失败等不给用户看崩栈
        print(f"运行出错（{type(e).__name__}）：{e}")
        print("排查建议：检查网络连通性、MCP_TOKEN 是否有效、list_tools 输出是否与 TOOL_KEYWORDS 匹配")
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
