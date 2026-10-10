# -*- coding: utf-8 -*-
"""
凑单引擎（核心模块）

职责：给定预算、人数、口味偏好、门店菜单与已领取优惠券，
输出券后最省钱的 Top3 套餐方案。

算法说明：
1. 口味过滤：剔除与用户忌口冲突的商品（如"不吃辣"）。
2. 券组合枚举：麦当劳券通常不可叠加，因此把"可用的券子集"枚举一遍
   （券数量一般 < 10，子集规模可控；超过阈值时退化为贪心选券）。
3. 启发式背包：在每个券组合下，以"单位饱腹感价格"为性价比指标做贪心背包，
   再用局部交换（1-1 swap）做一次改进，逼近最优。
4. 双维度排序：分别按"最便宜"和"吃得最饱"输出 Top3。
5. 预算无解：返回最接近预算的方案，并说明差额。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from itertools import combinations

# 券子集枚举的规模上限；超过则退化为贪心，避免组合爆炸
MAX_COUPON_ENUM = 8
# 单个商品最多重复购买份数（穷鬼套餐常靠多件低价单品堆饱腹感）
MAX_ITEM_QTY = 3


def _r2(x: float) -> float:
    """金额统一两位小数收口。+1e-9 抵消 float 尾差（如 0.1*3=0.30000000000000004），
    保证对外展示的数字可复现。所有 Plan 金额必须经此函数。"""
    return round(x + 1e-9, 2)


@dataclass
class MenuItem:
    """菜单商品"""
    item_id: str
    name: str
    price: float          # 原价（元）
    category: str = "other"   # staple主食 / snack小食 / drink饮品 / dessert甜品 / other
    is_veg: bool = False      # 是否纯素
    is_spicy: bool = False    # 是否辣
    satiety: float = 1.0      # 饱腹感评分（粗略：主食3 小食2 甜品1 饮品0.5，可由营养数据修正）
    available: bool = True    # 是否有货


@dataclass
class Coupon:
    """优惠券。discount 为立减金额；min_spend 为使用门槛；applicable_ids 为空表示全场通用"""
    coupon_id: str
    title: str
    discount: float
    min_spend: float = 0.0
    applicable_ids: list[str] = field(default_factory=list)  # 空 = 通用券


@dataclass
class PlanItem:
    item: MenuItem
    qty: int


@dataclass
class Plan:
    """一个凑单方案"""
    items: list[PlanItem]
    coupons_used: list[Coupon]
    original_price: float     # 原价合计
    final_price: float        # 券后实付（含配送费，如有）
    saved: float              # 立省金额
    total_satiety: float      # 总饱腹感
    delivery_fee: float = 0.0  # 配送费（自取为 0）
    note: str = ""            # 备注（如"超预算差额说明"）


def filter_by_preference(items: list[MenuItem],
                         no_spicy: bool = False,
                         vegetarian: bool = False) -> list[MenuItem]:
    """按口味偏好过滤菜单，同时剔除缺货商品"""
    result = []
    for it in items:
        if not it.available:
            continue
        if no_spicy and it.is_spicy:
            continue
        if vegetarian and not it.is_veg:
            continue
        result.append(it)
    return result


def _coupon_applicable(coupon: Coupon, plan_items: list[PlanItem]) -> bool:
    """判断券是否适用于当前购物筐：通用券直接可用；定向券要求筐内有对应商品且达门槛"""
    subtotal = sum(p.item.price * p.qty for p in plan_items)
    if subtotal < coupon.min_spend:
        return False
    if not coupon.applicable_ids:
        return True
    return any(p.item.item_id in coupon.applicable_ids for p in plan_items)


def _apply_coupons(plan_items: list[PlanItem],
                   coupons: list[Coupon]) -> tuple[float, list[Coupon]]:
    """对给定购物筐应用一组券（不可叠加：实际只取其中最优的一张可用券；
    若业务允许叠加，改为累加即可）。返回 (券后价, 实际用券列表)"""
    subtotal = sum(p.item.price * p.qty for p in plan_items)
    usable = [c for c in coupons if _coupon_applicable(c, plan_items)]
    if not usable:
        return subtotal, []
    # 不可叠加：选立减最大的一张
    best = max(usable, key=lambda c: c.discount)
    final = max(0.0, subtotal - best.discount)
    return final, [best]


def _greedy_knapsack(items: list[MenuItem], budget: float,
                     headcount: int) -> list[PlanItem]:
    """启发式背包：按 饱腹感/价格 性价比降序贪心装筐。
    目标饱腹感 = 人数 * 3（人均一份主食级别的饱）。"""
    target_satiety = headcount * 3.0
    # 展开为可重复选取的候选列表（限 MAX_ITEM_QTY 份）
    candidates: list[MenuItem] = []
    for it in items:
        for _ in range(MAX_ITEM_QTY):
            candidates.append(it)
    # 性价比：每元钱买到的饱腹感；同价比单价低的（穷鬼哲学）
    candidates.sort(key=lambda x: (x.satiety / max(x.price, 0.01), -x.price), reverse=True)

    basket: dict[str, PlanItem] = {}
    cost = 0.0
    satiety = 0.0
    for it in candidates:
        if satiety >= target_satiety and cost > 0:
            break
        if cost + it.price > budget:
            continue
        qty = basket[it.item_id].qty + 1 if it.item_id in basket else 1
        if qty > MAX_ITEM_QTY:
            continue
        basket[it.item_id] = PlanItem(item=it, qty=qty)
        cost += it.price
        satiety += it.satiety
    return list(basket.values())


def _local_swap_improve(plan_items: list[PlanItem], items: list[MenuItem],
                        budget: float) -> list[PlanItem]:
    """局部改进：尝试用一件未选商品替换筐内一件商品，
    若饱腹感不降且更便宜（或同价更饱），则接受交换。只跑一轮，控制耗时。"""
    current = {p.item.item_id: p for p in plan_items}
    cost = sum(p.item.price * p.qty for p in plan_items)
    satiety = sum(p.item.satiety * p.qty for p in plan_items)

    chosen_ids = set(current.keys())
    improved = False
    for out_id, out_p in list(current.items()):
        for cand in items:
            if cand.item_id in chosen_ids:
                continue
            new_cost = cost - out_p.item.price * out_p.qty + cand.price
            new_satiety = satiety - out_p.item.satiety * out_p.qty + cand.satiety
            if new_cost > budget:
                continue
            # 更便宜且不更饿，或同价更饱 → 换
            if (new_cost < cost and new_satiety >= satiety) or \
               (abs(new_cost - cost) < 1e-6 and new_satiety > satiety):
                del current[out_id]
                current[cand.item_id] = PlanItem(item=cand, qty=1)
                cost, satiety = new_cost, new_satiety
                improved = True
                break
        if improved:
            break
    return list(current.values())


def _coupon_subsets(coupons: list[Coupon]):
    """枚举券子集。券不可叠加时其实只需枚举单张+不用券，
    但保留子集枚举以兼容未来"可叠加"规则；数量超限退化为按面额贪心取前 N。"""
    if len(coupons) > MAX_COUPON_ENUM:
        coupons = sorted(coupons, key=lambda c: c.discount, reverse=True)[:MAX_COUPON_ENUM]
    yield []  # 不用券的情况
    for r in range(1, len(coupons) + 1):
        for combo in combinations(coupons, r):
            yield list(combo)


def _is_balanced(plan_items: list[PlanItem]) -> bool:
    """荤素搭配判定：筐内同时包含主食(staple)与非主食(小食/饮品/甜品)。
    全主食或全零食都视为搭配不均衡。"""
    cats = {p.item.category for p in plan_items}
    return "staple" in cats and len(cats) > 1


def generate_plans(items: list[MenuItem],
                   coupons: list[Coupon],
                   budget: float,
                   headcount: int,
                   no_spicy: bool = False,
                   vegetarian: bool = False,
                   delivery_fee: float = 0.0,
                   top_k: int = 3) -> dict:
    """
    主入口：生成凑单方案。

    delivery_fee：外送配送费（自取传 0）。预算口径为"含配送费的总实付"，
    即凑单可用额度 = budget - delivery_fee。

    返回 {
        "cheapest": [Plan, ...],   # 按最便宜排序的 TopK
        "fullest":  [Plan, ...],   # 按吃得最饱排序的 TopK
        "over_budget": bool,       # 预算是否无解
        "gap": float,              # 无解时，最接近方案与预算的差额
        "same_plan": bool          # 无解兜底时两维度为同一方案（内容为等价拷贝，
                                   # 但 cheapest/fullest 列表里是互相独立的对象，可安全分别修改）
    }
    """
    # 人数校验：headcount <= 0 无意义，直接返回空方案
    if headcount <= 0:
        return {"cheapest": [], "fullest": [], "over_budget": True,
                "gap": _r2(budget), "same_plan": False}

    # 预算口径统一为"含配送费总实付"：凑单额度先扣配送费
    shop_budget = budget - max(0.0, delivery_fee)
    delivery_fee = max(0.0, delivery_fee)

    pool = filter_by_preference(items, no_spicy=no_spicy, vegetarian=vegetarian)
    plans: list[Plan] = []
    seen_signatures: set[tuple] = set()

    for coupon_set in _coupon_subsets(coupons):
        coupon_boost = max((c.discount for c in coupon_set), default=0.0)
        basket = _greedy_knapsack(pool, shop_budget + coupon_boost, headcount)
        if not basket:
            continue
        basket = _local_swap_improve(basket, pool, shop_budget + coupon_boost)
        original = _r2(sum(p.item.price * p.qty for p in basket))
        coupon_price, used = _apply_coupons(basket, coupon_set)
        final = _r2(coupon_price + delivery_fee)  # 实付 = 券后商品价 + 配送费
        satiety = _r2(sum(p.item.satiety * p.qty for p in basket))
        # 去重：同样的商品组合+用券结果只保留一份
        sig = (tuple(sorted((p.item.item_id, p.qty) for p in basket)),
               tuple(sorted(c.coupon_id for c in used)))
        if sig in seen_signatures:
            continue
        seen_signatures.add(sig)
        note = f"含配送费 ¥{delivery_fee:.2f}" if delivery_fee > 0 else ""
        plans.append(Plan(items=basket, coupons_used=used,
                          original_price=original,
                          final_price=final,
                          saved=_r2(original - coupon_price),
                          total_satiety=satiety,
                          delivery_fee=delivery_fee,
                          note=note))

    if not plans:
        # 菜单全空 / 全缺货
        return {"cheapest": [], "fullest": [], "over_budget": True,
                "gap": _r2(budget), "same_plan": False}

    # 预算内方案优先；超预算方案保留用于"最接近"兜底
    within = [p for p in plans if p.final_price <= budget]
    over = [p for p in plans if p.final_price > budget]

    if not within:
        # 预算无解：找最接近预算的方案，标注差额。
        # cheapest/fullest 返回两份独立拷贝，调用方可安全分别加工，互不污染。
        closest = min(over, key=lambda p: p.final_price - budget)
        gap = _r2(closest.final_price - budget)
        closest.note = f"预算不足，这是最接近的方案，还差 ¥{gap:.2f}"
        return {"cheapest": [closest], "fullest": [replace(closest)],
                "over_budget": True, "gap": gap, "same_plan": True}

    # 荤素搭配优先：搭配均衡的方案排前面（软约束，不剔除，避免无解场景被卡死）
    def _balanced_key(p: Plan) -> int:
        return 0 if _is_balanced(p.items) else 1

    cheapest = sorted(within,
                      key=lambda p: (_balanced_key(p), p.final_price, -p.total_satiety))[:top_k]
    fullest = sorted(within,
                     key=lambda p: (_balanced_key(p), -p.total_satiety, p.final_price))[:top_k]
    # 若 Top1 仍不均衡，加备注提示（不阻断；追加而非覆盖，保留配送费等已有备注）
    for p in cheapest:
        if not _is_balanced(p.items):
            tip = "搭配单一，可考虑加份小食/饮品"
            p.note = f"{p.note}；{tip}" if p.note else tip
    return {"cheapest": cheapest, "fullest": fullest,
            "over_budget": False, "gap": 0.0, "same_plan": False}


def render_battle_report(plan: Plan, headcount: int,
                         coupon_verified: bool = True) -> str:
    """生成可直接分享的省钱战报文案。
    coupon_verified=False 表示用券未在下单前复核（或复核失败重凑），
    文案会加有效性标注，避免晒单后被打脸。"""
    lines = [
        "🍔 麦门穷鬼套餐战报",
        f"本单 {headcount} 人餐：原价 ¥{plan.original_price:.2f}，"
        f"券后 ¥{plan.final_price:.2f}，立省 ¥{plan.saved:.2f}！",
    ]
    if plan.delivery_fee > 0:
        lines.append(f"（含配送费 ¥{plan.delivery_fee:.2f}）")
    if plan.coupons_used:
        used = "、".join(f"「{c.title}」" for c in plan.coupons_used)
        lines.append(f"用券：{used}")
        if not coupon_verified:
            lines.append("（用券最终以下单时系统校验为准）")
    detail = " + ".join(f"{p.item.name}×{p.qty}" for p in plan.items)
    lines.append(f"配置：{detail}")
    lines.append("#麦门穷鬼套餐 #麦当劳MCP")
    return "\n".join(lines)
