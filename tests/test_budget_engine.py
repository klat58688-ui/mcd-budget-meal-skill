# -*- coding: utf-8 -*-
"""凑单引擎单元测试。运行：python -m pytest tests/ -v （无 pytest 时可用 python -m unittest）"""

import unittest
from unittest import mock

from src.budget_engine import (
    Coupon, MenuItem, filter_by_preference, generate_plans, render_battle_report,
)

# ---------- 测试夹具：一份简化的穷鬼菜单 ----------

def make_menu() -> list[MenuItem]:
    return [
        MenuItem("b1", "麦辣鸡腿堡", 21.0, "staple", is_spicy=True, satiety=3.0),
        MenuItem("b2", "板烧鸡腿堡", 22.0, "staple", satiety=3.0),
        MenuItem("b3", "麦香鸡", 14.0, "staple", satiety=2.5),
        MenuItem("s1", "小薯条", 9.0, "snack", is_veg=True, satiety=1.5),
        MenuItem("s2", "麦乐鸡5块", 13.0, "snack", satiety=2.0),
        MenuItem("d1", "中杯可乐", 8.0, "drink", is_veg=True, satiety=0.5),
        MenuItem("d2", "冰淇淋", 5.0, "dessert", is_veg=True, satiety=1.0),
        MenuItem("x1", "售罄汉堡", 1.0, "staple", satiety=3.0, available=False),  # 缺货
    ]


def make_coupons() -> list[Coupon]:
    return [
        Coupon("c1", "满30减8券", 8.0, min_spend=30.0),
        Coupon("c2", "麦乐鸡立减5券", 5.0, applicable_ids=["s2"]),
        Coupon("c3", "全场立减2券", 2.0),
    ]


class TestFilter(unittest.TestCase):
    def test_no_spicy(self):
        pool = filter_by_preference(make_menu(), no_spicy=True)
        self.assertNotIn("b1", {i.item_id for i in pool})

    def test_vegetarian(self):
        pool = filter_by_preference(make_menu(), vegetarian=True)
        self.assertTrue(all(i.is_veg for i in pool))

    def test_unavailable_removed(self):
        pool = filter_by_preference(make_menu())
        self.assertNotIn("x1", {i.item_id for i in pool})


class TestGeneratePlans(unittest.TestCase):
    def test_budget_20_two_people(self):
        """核心场景：20 元两个人，券后必须 ≤ 20，荤素搭配有主食"""
        r = generate_plans(make_menu(), make_coupons(), budget=20, headcount=2)
        self.assertFalse(r["over_budget"])
        for plan in r["cheapest"]:
            self.assertLessEqual(plan.final_price, 20.0)
            self.assertGreater(plan.total_satiety, 0)
        # 至少给出了方案数
        self.assertGreaterEqual(len(r["cheapest"]), 1)

    def test_coupon_actually_saves(self):
        """用券方案应比同筐不用券便宜（或相等）"""
        r = generate_plans(make_menu(), make_coupons(), budget=30, headcount=2)
        for plan in r["cheapest"]:
            self.assertEqual(plan.original_price - plan.final_price, plan.saved)
            self.assertGreaterEqual(plan.saved, 0)

    def test_top3_sorted(self):
        r = generate_plans(make_menu(), make_coupons(), budget=40, headcount=2)
        prices = [p.final_price for p in r["cheapest"]]
        self.assertEqual(prices, sorted(prices))
        satieties = [p.total_satiety for p in r["fullest"]]
        self.assertEqual(satieties, sorted(satieties, reverse=True))

    def test_impossible_budget(self):
        """预算 1 元无解：返回最接近方案并说明差额"""
        r = generate_plans(make_menu(), make_coupons(), budget=1, headcount=2)
        self.assertTrue(r["over_budget"])
        self.assertGreater(r["gap"], 0)
        self.assertIn("还差", r["cheapest"][0].note)

    def test_no_spicy_respected(self):
        r = generate_plans(make_menu(), make_coupons(), budget=30, headcount=2,
                           no_spicy=True)
        for plan in r["cheapest"]:
            for p in plan.items:
                self.assertFalse(p.item.is_spicy)

    def test_targeted_coupon_threshold(self):
        """定向券：筐内没有适用品或未达门槛时不得使用"""
        r = generate_plans(make_menu(), make_coupons(), budget=20, headcount=2)
        for plan in r["cheapest"]:
            for c in plan.coupons_used:
                subtotal = plan.original_price
                self.assertGreaterEqual(subtotal, c.min_spend)
                if c.applicable_ids:
                    ids = {p.item.item_id for p in plan.items}
                    self.assertTrue(ids & set(c.applicable_ids))

    def test_balanced_plan_ranked_first(self):
        """荤素搭配：含主食+非主食的均衡方案应排在单一搭配之前"""
        r = generate_plans(make_menu(), make_coupons(), budget=30, headcount=2)
        top = r["cheapest"][0]
        cats = {p.item.category for p in top.items}
        self.assertIn("staple", cats)
        self.assertGreater(len(cats), 1)

    def test_item_qty_cap(self):
        """单商品购买份数不超过 MAX_ITEM_QTY"""
        r = generate_plans(make_menu(), make_coupons(), budget=60, headcount=4)
        for plan in r["cheapest"] + r["fullest"]:
            for p in plan.items:
                self.assertLessEqual(p.qty, 3)

    def test_zero_headcount_returns_empty(self):
        """headcount<=0：直接返回空方案，不得产出任何商品组合"""
        r = generate_plans(make_menu(), make_coupons(), budget=20, headcount=0)
        self.assertEqual(r["cheapest"], [])
        self.assertEqual(r["fullest"], [])
        self.assertTrue(r["over_budget"])

    def test_over_budget_dimensions_are_independent(self):
        """预算无解时 cheapest 与 fullest 必须是两个独立对象，且有 same_plan 标记"""
        r = generate_plans(make_menu(), make_coupons(), budget=1, headcount=2)
        self.assertTrue(r["over_budget"])
        self.assertTrue(r["same_plan"])  # 明确标记：两维度内容等价
        a, b = r["cheapest"][0], r["fullest"][0]
        self.assertIsNot(a, b)           # 但不是同一个对象，可安全分别修改
        self.assertEqual(a.final_price, b.final_price)

    def test_delivery_fee_counts_into_budget(self):
        """外送：配送费计入预算口径，final_price 含配送费"""
        r = generate_plans(make_menu(), make_coupons(), budget=20, headcount=2,
                           delivery_fee=9.0)
        for plan in r["cheapest"]:
            self.assertLessEqual(plan.final_price, 20.0)
            self.assertEqual(plan.delivery_fee, 9.0)
            self.assertIn("配送费", plan.note)


class TestBattleReport(unittest.TestCase):
    def test_report_contains_numbers(self):
        r = generate_plans(make_menu(), make_coupons(), budget=20, headcount=2)
        plan = r["cheapest"][0]
        text = render_battle_report(plan, 2)
        self.assertIn(f"¥{plan.original_price:.2f}", text)
        self.assertIn(f"¥{plan.final_price:.2f}", text)
        self.assertIn(f"立省 ¥{plan.saved:.2f}", text)
        self.assertIn("#麦门穷鬼套餐", text)


class TestMcpClientAuthMessage(unittest.TestCase):
    def test_401_friendly_message(self):
        """401/403 必须抛出人能看懂的 Token 提示，而不是原始响应体"""
        from src.mcp_client import McdMcpClient, McpError
        client = McdMcpClient(token="fake-token")
        fake_resp = mock.Mock()
        fake_resp.status_code = 401
        fake_resp.headers = {}
        fake_ctx = mock.Mock()
        fake_ctx.__enter__ = mock.Mock(return_value=fake_resp)
        fake_ctx.__exit__ = mock.Mock(return_value=False)
        client._client = mock.Mock()
        client._client.stream = mock.Mock(return_value=fake_ctx)
        with self.assertRaises(McpError) as cm:
            client._send({"jsonrpc": "2.0", "id": "x", "method": "tools/list"})
        self.assertIn("MCP_TOKEN 无效或已过期", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
