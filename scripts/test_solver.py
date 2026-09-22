"""求解器行为测试：可直接 `python scripts/test_solver.py` 运行。"""
import sys

sys.path.insert(0, ".")
from app.solver import BUDGET, FAIL, OK, PSpec, RuleSpec, Solver, TSpec


def ps(ids):
    return [PSpec(i, f"p{i}") for i in ids]


def ts(defs):
    # (id, name, capacity, section)
    return [TSpec(i, f"t{i}", cap, sec) for i, cap, sec in defs]


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)
    print("  ✓", msg)


print("[1] 基础求解 + 容量")
s = Solver(ps(range(1, 9)), ts([(1, 8, None), (2, 8, None)]), {}, [], {}, seed=1)
r = s.solve_full()
check(r.status == OK, "8 人 2 桌有解")
check(len(r.assignments) == 8, "8 人全部安排")
seats = list(r.assignments.values())
check(len(set(seats)) == 8, "席位不重复")

print("[2] 可重复性：相同种子结果一致")
s1 = Solver(ps(range(1, 13)), ts([(1, 6, None), (2, 6, None)]), {}, [], {}, seed=42)
s2 = Solver(ps(range(1, 13)), ts([(1, 6, None), (2, 6, None)]), {}, [], {}, seed=42)
r1, r2 = s1.solve_full(), s2.solve_full()
check(r1.assignments == r2.assignments, "seed=42 两次结果完全一致")
s3 = Solver(ps(range(1, 13)), ts([(1, 6, None), (2, 6, None)]), {}, [], {}, seed=43)
r3 = s3.solve_full()
check(r3.status == OK, "不同种子也有解")

print("[3] together 规则")
s = Solver(
    ps(range(1, 11)),
    ts([(1, 5, None), (2, 5, None)]),
    {},
    [RuleSpec(1, "together", (1, 2, 3, 4, 5), None, None)],
    {},
    seed=7,
)
r = s.solve_full()
check(r.status == OK, "5 人必须同桌有解")
tabs = {r.assignments[i][0] for i in (1, 2, 3, 4, 5)}
check(len(tabs) == 1, "5 人坐在同一桌")

print("[4] separate 规则")
s = Solver(
    ps(range(1, 7)),
    ts([(1, 3, None), (2, 3, None), (3, 3, None)]),
    {},
    [RuleSpec(1, "separate", (1, 2, 3), None, None)],
    {},
    seed=1,
)
r = s.solve_full()
check(r.status == OK, "3 人禁止同桌（3 桌）有解")
tabs = {r.assignments[i][0] for i in (1, 2, 3)}
check(len(tabs) == 3, f"3 人分布在 3 张不同桌：{tabs}")

print("[5] 无解：separate 超过桌数，冲突定位到具体规则")
s = Solver(
    ps(range(1, 5)),
    ts([(1, 4, None)]),
    {},
    [RuleSpec(1, "separate", (1, 2, 3), None, None)],
    {},
)
r = s.solve_full()
check(r.status == FAIL, "3 人在唯一一桌必须不同桌 => 无解")
check(("rule", 1) in r.conflicts, f"冲突集合包含规则1：{r.conflicts}")

print("[6] fixed 规则（精确席位）")
s = Solver(
    ps(range(1, 5)),
    ts([(1, 4, None)]),
    {},
    [RuleSpec(1, "fixed", (2,), 1, 3)],
    {},
)
r = s.solve_full()
check(r.status == OK, "固定 p2 到 t1 第3席有解")
check(r.assignments[2] == (1, 3), "p2 确实在 (桌1, 席3)")

print("[7] 两条 fixed 精确席位撞车 => 定位到这两条规则")
s = Solver(
    ps(range(1, 5)),
    ts([(1, 4, None)]),
    {},
    [
        RuleSpec(1, "fixed", (1,), 1, 2),
        RuleSpec(2, "fixed", (2,), 1, 2),
    ],
    {},
)
r = s.solve_full()
check(r.status == FAIL, "两人固定到同一席位 => 无解")
check(("rule", 1) in r.conflicts and ("rule", 2) in r.conflicts,
      f"冲突同时包含规则1与规则2：{r.conflicts}")

print("[8] 锁定后重新求解，锁定保持")
s = Solver(
    ps(range(1, 7)),
    ts([(1, 4, None), (2, 4, None)]),
    {},
    [],
    {3: (2, 1)},  # p3 锁定到桌2席1
    seed=5,
)
r = s.solve_full()
check(r.status == OK, "锁定场景有解")
check(r.assignments[3] == (2, 1), "锁定的 p3 仍在桌2席1")

print("[9] 锁定与规则冲突 => 同时指出规则与锁定")
s = Solver(
    ps(range(1, 6)),
    ts([(1, 4, None), (2, 4, None)]),
    {},
    [RuleSpec(1, "separate", (1, 2), None, None)],
    {1: (1, 1), 2: (1, 2)},  # 两张桌，p1/p2 本可分桌，但被锁到同桌
)
r = s.solve_full()
check(r.status == FAIL, "锁定与禁止同桌冲突 => 无解")
kinds = set(r.conflicts)
check(("rule", 1) in kinds and ("lock", 1) in kinds and ("lock", 2) in kinds,
      f"冲突包含规则1及两个锁定：{r.conflicts}")

print("[10] 删除法极小性：各自可满足、合起来矛盾的两条规则同时入核")
# 两张各 3 座的桌，5 个人：together(1,2,3) 单独可解，separate(1,2) 单独可解，
# 合起来矛盾（1、2 既要同桌又要分桌）
s = Solver(
    ps(range(1, 6)),
    ts([(1, 3, None), (2, 3, None)]),
    {},
    [
        RuleSpec(1, "separate", (1, 2), None, None),
        RuleSpec(2, "together", (1, 2, 3), None, None),
    ],
    {},
)
r = s.solve_full()
check(r.status == FAIL, "两条互相矛盾规则 => 无解")
check({c for c in r.conflicts if c[0] == "rule"} == {("rule", 1), ("rule", 2)},
      f"极小核心恰好是两条规则：{r.conflicts}")

print("[11] 总席位不足 => capacity 标签")
s = Solver(ps(range(1, 11)), ts([(1, 5, None), (2, 4, None)]), {}, [], {})
r = s.solve_full()
check(r.status == FAIL, "10 人 9 座 => 无解")
check(r.conflicts == [("capacity", None)], f"冲突为容量不足：{r.conflicts}")

print("[12] 分区容量约束")
s = Solver(
    ps(range(1, 7)),
    [TSpec(1, "t1", 10, 100), TSpec(2, "t2", 10, 200)],
    {100: 2},  # A 区最多 2 人
    [],
    {},
    seed=3,
)
r = s.solve_full()
check(r.status == OK, "分区约束下有解")
in_a = sum(1 for pid, (tid, _) in r.assignments.items() if tid == 1)
check(in_a <= 2, f"A 区人数不超过 2（实际 {in_a}）")

print("[13] 组人数超过单桌容量 => 指出 together 规则")
s = Solver(
    ps(range(1, 7)),
    [TSpec(1, "t1", 4, 100), TSpec(2, "t2", 4, 200)],
    {100: 4, 200: 4},
    [RuleSpec(1, "together", (1, 2, 3, 4, 5), None, None)],
    {},
)
r = s.solve_full()
check(r.status == FAIL, "5 人必须同桌但每桌仅 4 座 => 无解")
check(("rule", 1) in r.conflicts, f"指出规则1：{r.conflicts}")

print("[14] 节点预算")
s = Solver(ps(range(1, 50)), ts([(i, 2, None) for i in range(1, 26)]),
           {}, [], {}, seed=1, node_limit=3)
r = s.solve_full()
check(r.status == BUDGET, "极小节点预算返回 budget")

print("\n全部测试通过 ✅")
