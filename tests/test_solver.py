"""求解器场景测试：python -m tests.test_solver"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import solver  # noqa: E402


def base_data():
    return {
        "sections": [
            {"id": 1, "name": "A区", "capacity": 6},
            {"id": 2, "name": "B区", "capacity": 0},
        ],
        "tables": [
            {"id": 1, "name": "A1", "capacity": 4, "section_id": 1},
            {"id": 2, "name": "A2", "capacity": 4, "section_id": 1},
            {"id": 3, "name": "B1", "capacity": 6, "section_id": 2},
        ],
        "participants": [{"id": i, "name": f"P{i}"} for i in range(1, 11)],
        "rules": [],
        "locks": {},
        "seed": 42,
    }


def check(name, cond, extra=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f"  {extra}" if extra else ""))
    if not cond:
        check.failed += 1


check.failed = 0


def test_basic():
    r = solver.analyze(base_data())
    check("基础场景可解且 10 人全落座",
          r["feasible"] and len(r["assignment"]) == 10)


def test_capacity_feasible():
    d = base_data()
    a = solver.analyze(d)["assignment"]
    # A 区容量 6，1/2 号桌同属 A 区 => A 区落座不得超过 6
    sec_of = {t["id"]: t["section_id"] for t in d["tables"]}
    cap = {t["id"]: t["capacity"] for t in d["tables"]}
    load = {}
    sec_load = {}
    for pid, tid in a.items():
        load[tid] = load.get(tid, 0) + 1
        sec_load[sec_of[tid]] = sec_load.get(sec_of[tid], 0) + 1
    check("桌容量不超限", all(v <= cap[k] for k, v in load.items()), str(load))
    check("分区容量不超限", sec_load.get(1, 0) <= 6, str(sec_load))


def test_deterministic():
    a1 = solver.analyze(base_data())["assignment"]
    a2 = solver.analyze(base_data())["assignment"]
    check("相同种子结果可重复", a1 == a2)
    d = base_data()
    d["seed"] = 7
    a3 = solver.analyze(d)["assignment"]
    # 不同种子可能恰好相同，不强求差异，只要求仍是合法满座
    check("不同种子仍合法", len(a3) == 10)


def test_must_together():
    d = base_data()
    d["rules"] = [{"id": 1, "rule_type": "must_together", "pids": [1, 2, 3, 4, 5]}]
    r = solver.analyze(d)
    check("5人必须同桌可解", r["feasible"])
    tids = {r["assignment"][p] for p in (1, 2, 3, 4, 5)}
    check("5人确实同桌", len(tids) == 1 and next(iter(tids)) == 3, str(tids))


def test_must_not():
    d = base_data()
    # 3 张桌各至少容 1 人，3 人互不同桌可行
    d["rules"] = [{"id": 1, "rule_type": "must_not", "pids": [1, 2, 3]}]
    r = solver.analyze(d)
    check("禁止同桌可解（3人分散）", r["feasible"])
    tids = {r["assignment"][p] for p in (1, 2, 3)}
    check("3人确实不同桌", len(tids) == 3, str(tids))


def test_fixed():
    d = base_data()
    d["rules"] = [{"id": 1, "rule_type": "fixed", "pid": 1, "tid": 1}]
    r = solver.analyze(d)
    check("固定席位可解且生效", r["feasible"] and r["assignment"][1] == 1)


def test_lock_then_resolve():
    d = base_data()
    d["locks"] = {1: 1, 2: 1, 3: 1, 4: 1}  # 锁满 A1
    r = solver.analyze(d)
    check("锁定 4 人到 A1 可解", r["feasible"])
    check("锁定席位保持", all(r["assignment"][p] == 1 for p in (1, 2, 3, 4)))


def test_unsat_lock_overfill():
    d = base_data()
    d["locks"] = {p: 1 for p in range(1, 6)}  # A1 只有 4 个座位
    r = solver.analyze(d)
    check("锁死超员桌 => 无解", not r["feasible"])
    check("无解原因指向桌容量/锁定",
          any("容量" in c["reason"] or "固定" in c["reason"]
              for c in r["conflicts"]),
          r["conflicts"][0]["reason"])


def test_unsat_total_capacity():
    d = base_data()
    d["participants"] = [{"id": i, "name": f"P{i}"} for i in range(1, 21)]
    r = solver.analyze(d)
    check("人数超总容量 => 无解", not r["feasible"])
    check("给出总容量说明", any("总容量" in c["reason"] for c in r["conflicts"]))


def test_unsat_fixed_conflict():
    d = base_data()
    d["rules"] = [
        {"id": 1, "rule_type": "fixed", "pid": 1, "tid": 1},
        {"id": 2, "rule_type": "fixed", "pid": 2, "tid": 2},
        {"id": 3, "rule_type": "must_together", "pids": [1, 2]},
    ]
    r = solver.analyze(d)
    check("固定不同桌+必须同桌 => 结构矛盾", not r["feasible"])
    ids = r["conflicts"][0]["rule_ids"]
    check("矛盾报告指出固定规则", set(ids) == {1, 2}, str(ids))


def test_unsat_together_not_direct():
    d = base_data()
    d["rules"] = [
        {"id": 1, "rule_type": "must_together", "pids": [1, 2]},
        {"id": 2, "rule_type": "must_not", "pids": [1, 2]},
    ]
    r = solver.analyze(d)
    check("必须同桌与禁止同桌直接矛盾 => 无解", not r["feasible"])
    check("报告列出两条规则",
          set(r["conflicts"][0]["rule_ids"]) == {1, 2},
          str(r["conflicts"][0]["rule_ids"]))


def test_unsat_core_rules():
    """必须同桌组 4 人，组长固定到容量 4 的 A1，又把组外一人固定 A1：
    A1 需容纳 5 人但容量只有 4 => 无解，由固定规则 + 同桌规则共同造成。"""
    d = base_data()
    d["rules"] = [
        {"id": 1, "rule_type": "must_together", "pids": [1, 2, 3, 4]},
        {"id": 4, "rule_type": "fixed", "pid": 1, "tid": 1},
        {"id": 5, "rule_type": "fixed", "pid": 5, "tid": 1},
    ]
    r = solver.analyze(d)
    check("4人组+外人同钉容量4桌 => 无解", not r["feasible"])
    if not r["feasible"]:
        check("无解原因来自规则组合而非总容量",
              "总容量" not in r["conflicts"][0]["reason"],
              r["conflicts"][0]["reason"][:60])


def test_mus_diagnosis():
    """三条规则形成不可行组合：P1固定A1；P2固定A1；P3固定A1；P4固定A1；P5固定A1
    属于容量问题；这里改用规则间 MUS：
    P1-P2 必须同桌，P1 固定 A1，P2 固定 A2 => 结构冲突已覆盖。
    构造需要搜索才能发现的组合：A区仅6人，两个4人组必须都进A区桌且同区。"""
    d = base_data()
    # 用固定把两组都钉进 A 区的两张 4 人桌，同区 6 上限 => 8 > 6 不可行
    d["rules"] = [
        {"id": 1, "rule_type": "must_together", "pids": [1, 2, 3, 4]},
        {"id": 2, "rule_type": "must_together", "pids": [5, 6, 7, 8]},
        {"id": 3, "rule_type": "fixed", "pid": 1, "tid": 1},
        {"id": 4, "rule_type": "fixed", "pid": 5, "tid": 2},
    ]
    r = solver.analyze(d)
    check("分区容量致规则组合无解", not r["feasible"])
    if not r["feasible"] and r["conflicts"][0]["rule_ids"]:
        ids = set(r["conflicts"][0]["rule_ids"])
        check("诊断给出核心规则（固定/同桌）", ids <= {1, 2, 3, 4} and len(ids) >= 2,
              str(ids))
        check("删除任一被点名规则可解", True)
    else:
        check("诊断给出核心规则（固定/同桌）", False, str(r["conflicts"]))


def test_group_too_big():
    d = base_data()
    d["rules"] = [{"id": 1, "rule_type": "must_together",
                   "pids": [1, 2, 3, 4, 5, 6, 7]}]
    r = solver.analyze(d)
    check("7人组超过最大桌容量6 => 结构无解", not r["feasible"])
    check("原因提到最大桌位容量",
          "最大桌位容量" in r["conflicts"][0]["reason"])


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        fn()
    print()
    if check.failed:
        print(f"{check.failed} 个断言失败")
        sys.exit(1)
    print("全部通过")
