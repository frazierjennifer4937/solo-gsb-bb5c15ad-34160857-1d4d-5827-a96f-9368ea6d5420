"""约束求解器：确定性回溯搜索 + 无解时的核心规则诊断。

输入均为普通 dict/list（见 analyze()），不依赖 ORM，便于单独测试。

支持的约束：
- 分区容量（每分区内桌位总座位数上限，cap=0 表示不限）
- 桌位容量
- 必须同桌（must_together，传递闭包，一个单位整体落座）
- 禁止同桌（must_not，桌内任意两人不得同属一条禁止关系）
- 固定席位（fixed，某人必须在某桌）
- 锁定席位（locked，用户在上一轮结果上手动锁定，语义等同固定席位，
  但属于"用户操作"，诊断无解时不会被列为可删除的编排规则）

可重复性：同一 seed + 同一输入 => 同一结果（random.Random(seed) 决定平局顺序）。
"""
from __future__ import annotations

import random
import time
from collections import defaultdict


# ---------------------------------------------------------------- 数据规整

def _build_inputs(data: dict, include_fixed: bool = True):
    participants = list(data.get("participants", []))
    tables = list(data.get("tables", []))
    rules = list(data.get("rules", []))
    sections = {s["id"]: s for s in data.get("sections", [])}

    pid_set = {p["id"] for p in participants}
    tid_set = {t["id"] for t in tables}

    table_of = {}          # pid -> tid，固定席位 + 锁定席位
    fixed_pid = {}         # pid -> tid，仅固定席位规则
    locked_pids = set()    # 被用户锁定的 pid
    fix_sources = []       # {kind:'fixed'|'lock', pid, tid, rule_id?}

    # 锁定席位优先录入
    for pid, tid in (data.get("locks") or {}).items():
        table_of[pid] = tid
        locked_pids.add(pid)
        fix_sources.append({"kind": "lock", "pid": pid, "tid": tid})

    must_together = []     # [{id, members:[pid...]}]
    must_not_edges = []    # [(a, b, rule_id)]
    for r in rules:
        kind = r["rule_type"]
        if kind == "must_together":
            must_together.append({"id": r["id"], "members": list(r["pids"])})
        elif kind == "must_not":
            for i in range(len(r["pids"])):
                for j in range(i + 1, len(r["pids"])):
                    must_not_edges.append((r["pids"][i], r["pids"][j], r["id"]))
        elif kind == "fixed" and include_fixed:
            fixed_pid[r["pid"]] = r["tid"]
            fix_sources.append(
                {"kind": "fixed", "pid": r["pid"], "tid": r["tid"], "rule_id": r["id"]}
            )
            # 固定席位规则覆盖锁定（规则是更强的显式声明）
            table_of[r["pid"]] = r["tid"]
            locked_pids.discard(r["pid"])

    return {
        "participants": participants,
        "tables": tables,
        "sections": sections,
        "rules": rules,
        "pid_set": pid_set,
        "tid_set": tid_set,
        "table_of": table_of,
        "fixed_pid": fixed_pid,
        "locked_pids": locked_pids,
        "fix_sources": fix_sources,
        "must_together": must_together,
        "must_not_edges": must_not_edges,
    }


def _build_units(inp: dict):
    """根据必须同桌规则（传递闭包）构造落座单位。返回 (units, pid->unit_idx)。"""
    parent = {pid: pid for pid in inp["pid_set"]}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for grp in inp["must_together"]:
        members = [p for p in grp["members"]]
        for m in members[1:]:
            union(members[0], m)

    groups = defaultdict(list)
    for pid in inp["pid_set"]:
        groups[find(pid)].append(pid)

    units = [sorted(members) for members in groups.values()]
    units.sort(key=lambda u: (-len(u), u[0]))
    pid_unit = {}
    for idx, u in enumerate(units):
        for pid in u:
            pid_unit[pid] = idx
    return units, pid_unit


# ---------------------------------------------------------------- 结构预检

def _structural_checks(inp: dict, units, pid_unit, units_for_rules=None):
    """检查与容量无关的硬性矛盾。返回 conflict 列表（空=通过）。"""
    conflicts = []
    tid_name = {t["id"]: t["name"] for t in inp["tables"]}
    pid_name = {p["id"]: p["name"] for p in inp["participants"]}
    rules_by_id = {r["id"]: r for r in inp["rules"]}

    def nm(pid):
        return pid_name.get(pid, f"#{pid}")

    def tn(tid):
        return tid_name.get(tid, f"#{tid}")

    # 1. 引用完整性 / 锁定目标
    fixed_by_pid = defaultdict(list)
    for fs in inp["fix_sources"]:
        if fs["kind"] == "fixed":
            fixed_by_pid[fs["pid"]].append(fs)
        if fs["pid"] not in inp["pid_set"]:
            conflicts.append({
                "reason": f"席位指定引用了不存在的参与者 #{fs['pid']}",
                "rule_ids": [fs["rule_id"]] if fs.get("rule_id") else [],
            })
        if fs["tid"] not in inp["tid_set"]:
            label = "固定席位规则" if fs["kind"] == "fixed" else "锁定操作"
            rid = [fs["rule_id"]] if fs.get("rule_id") else []
            conflicts.append({
                "reason": f"{label}把 {nm(fs['pid'])} 指定到了不存在的桌位 #{fs['tid']}",
                "rule_ids": rid,
            })

    # 1b. 同一参与者被多条固定席位规则指到不同桌位
    for pid, items in fixed_by_pid.items():
        tids = {fs["tid"] for fs in items}
        if len(tids) > 1:
            rids = sorted({fs["rule_id"] for fs in items if fs.get("rule_id")})
            conflicts.append({
                "reason": (
                    f"参与者 {nm(pid)} 被多条固定席位规则指定到不同桌位"
                    f"（{'、'.join(tn(t) for t in sorted(tids))}），固定规则自相矛盾"
                ),
                "rule_ids": rids,
            })

    # 2. 同组固定到不同桌
    forced_tid = {}  # unit_idx -> tid
    for pid, tid in inp["table_of"].items():
        if pid not in pid_unit or tid not in inp["tid_set"]:
            continue
        ui = pid_unit[pid]
        if ui in forced_tid and forced_tid[ui] != tid:
            members = [nm(p) for p in units[ui]]
            involved = sorted({
                fs["rule_id"]
                for fs in inp["fix_sources"]
                if fs["kind"] == "fixed" and fs["pid"] in units[ui] and fs.get("rule_id")
            })
            tids = {fs["tid"]: True for fs in inp["fix_sources"]
                    if fs["pid"] in units[ui] and fs["tid"] in inp["tid_set"]}
            conflicts.append({
                "reason": (
                    f"必须同桌组（{'、'.join(members)}）中的成员被固定到了不同桌位"
                    f"（{'、'.join(tn(t) for t in sorted(tids))}），必须同桌与固定席位相互冲突"
                ),
                "rule_ids": involved,
            })
            break
        forced_tid[ui] = tid

    # 3. 组人数超过其被固定桌的容量
    tinfo = {t["id"]: t for t in inp["tables"]}
    for ui, tid in forced_tid.items():
        t = tinfo.get(tid)
        if t and len(units[ui]) > t["capacity"]:
            involved = sorted({
                fs["rule_id"] for fs in inp["fix_sources"]
                if fs["kind"] == "fixed" and pid_unit.get(fs["pid"]) == ui and fs.get("rule_id")
            })
            members = [nm(p) for p in units[ui]]
            conflicts.append({
                "reason": (
                    f"必须同桌组（{'、'.join(members)}，{len(units[ui])} 人）"
                    f"无法整体放入被固定的桌位「{tn(tid)}」（容量 {t['capacity']}）"
                ),
                "rule_ids": involved,
            })

    # 4. 禁止同桌：固定/锁定到同一桌
    for a, b, rid in inp["must_not_edges"]:
        ta, tb = inp["table_of"].get(a), inp["table_of"].get(b)
        if ta is not None and ta == tb:
            r = rules_by_id.get(rid)
            note = f"：{r['note']}" if r and r.get("note") else ""
            conflicts.append({
                "reason": (
                    f"禁止同桌规则要求 {nm(a)} 与 {nm(b)} 不同桌，"
                    f"但二人都被固定/锁定在「{tn(ta)}」{note}"
                ),
                "rule_ids": [rid],
            })

    # 5. 必须同桌与禁止同桌的直接矛盾（同一闭包内存在禁止边）
    for a, b, rid in inp["must_not_edges"]:
        if a in pid_unit and b in pid_unit and pid_unit[a] == pid_unit[b]:
            r = rules_by_id.get(rid)
            note = f"：{r['note']}" if r and r.get("note") else ""
            related_together = []
            if units_for_rules:
                related_together = units_for_rules.get(pid_unit[a], [])
            conflicts.append({
                "reason": (
                    f"禁止同桌要求 {nm(a)} 与 {nm(b)} 分开，但必须同桌规则又要求他们同桌，"
                    f"两类规则直接矛盾{note}"
                ),
                "rule_ids": sorted(set([rid] + related_together)),
            })

    # 6. 固定桌所属分区不存在（数据破损）
    for t in inp["tables"]:
        if t.get("section_id") is not None and t["section_id"] not in inp["sections"]:
            conflicts.append({
                "reason": f"桌位「{tn(t['id'])}」属于不存在的分区 #{t['section_id']}",
                "rule_ids": [],
            })

    # 7. 必须同桌组规模超过任何一张桌的容量
    max_cap = max((t["capacity"] for t in inp["tables"]), default=0)
    for ui, unit in enumerate(units):
        if len(unit) > 1 and len(unit) > max_cap:
            members = [nm(p) for p in unit]
            related = sorted(units_for_rules.get(ui, [])) if units_for_rules else []
            conflicts.append({
                "reason": (
                    f"必须同桌组（{'、'.join(members)}，{len(unit)} 人）"
                    f"超过最大桌位容量（{max_cap} 人），任何桌位都无法整组容纳"
                ),
                "rule_ids": related,
            })

    # 8. 禁止同桌形成的"互斥团"：团中每人都需独占一桌。
    #    可用桌位下界 = 能容纳单人的桌数 - 已被团外固定/锁定者占满的桌数。
    if inp["must_not_edges"] and inp["tables"]:
        adj = defaultdict(set)
        edge_rule = {}
        for a, b, rid in inp["must_not_edges"]:
            if a in inp["pid_set"] and b in inp["pid_set"]:
                adj[a].add(b)
                adj[b].add(a)
                edge_rule[(min(a, b), max(a, b))] = rid

        # 每张桌已被团外人员预置占用的座位
        preset_count = defaultdict(int)
        for pid, tid in inp["table_of"].items():
            preset_count[tid] += 1
        single_tables = sum(
            1 for t in inp["tables"]
            if t["capacity"] >= 1 and preset_count[t["id"]] < t["capacity"])
        limit = max(single_tables, 0)
        reported = set()

        def greedy_clique(seed_pid):
            clique = [seed_pid]
            candidates = set(adj[seed_pid])
            while candidates:
                nxt = min(candidates, key=lambda x: (-len(adj[x] & candidates), x))
                clique.append(nxt)
                candidates &= adj[nxt]
            return clique

        for pid0 in sorted(inp["pid_set"]):
            if not adj.get(pid0):
                continue
            clique = greedy_clique(pid0)
            if len(clique) > limit:
                key = tuple(sorted(clique))
                if key in reported:
                    continue
                reported.add(key)
                names = [nm(p) for p in clique]
                rids = sorted({
                    edge_rule[(min(a, b), max(a, b))]
                    for i, a in enumerate(clique) for b in clique[i + 1:]
                })
                conflicts.append({
                    "reason": (
                        f"参与者 {'、'.join(names)} 两两禁止同桌，共 {len(clique)} 人，"
                        f"每人都必须独占一张桌，但只有 {limit} 张桌还能放人"
                    ),
                    "rule_ids": rids,
                })
                if len(conflicts) > 20:
                    return conflicts

    return conflicts


# ---------------------------------------------------------------- 回溯搜索

def _search(inp: dict, units, pid_unit, *, use_rules: bool, seed: int,
            node_limit: int, deadline: float | None):
    """带前向检查的确定性回溯。use_rules=False 时忽略 must_not（容量基线检查）。

    返回 (assign: pid->tid 或 None, nodes, timed_out)。
    """
    rng = random.Random(seed)
    tinfo = {t["id"]: t for t in inp["tables"]}

    # 静态候选域：固定桌 / 容量放得下整组的桌
    domains = []
    forced = {}          # unit_idx -> tid
    forced_order = []    # 预置单位的放置顺序（确定性）
    for ui, unit in enumerate(units):
        ftids = {inp["table_of"][p] for p in unit if p in inp["table_of"]}
        if len(ftids) > 1:
            return None, 0, False  # 预检应已拦截
        if ftids:
            tid = next(iter(ftids))
            t = tinfo.get(tid)
            if t is None or t["capacity"] < len(unit):
                return None, 0, False
            forced[ui] = tid
            domains.append((tid,))
            forced_order.append(ui)
        else:
            dom = tuple(t["id"] for t in inp["tables"]
                        if t["capacity"] >= len(unit))
            if not dom:
                return None, 0, False
            domains.append(dom)

    # 禁止关系：pid -> set(pid)
    forbid = defaultdict(set)
    if use_rules:
        for a, b, _rid in inp["must_not_edges"]:
            forbid[a].add(b)
            forbid[b].add(a)

    n_tables = len(inp["tables"])
    table_load = [0] * n_tables
    table_idx = {t["id"]: i for i, t in enumerate(inp["tables"])}
    table_caps = [t["capacity"] for t in inp["tables"]]
    table_members = [set() for _ in range(n_tables)]
    sec_of_table = [t.get("section_id") for t in inp["tables"]]
    section_load = defaultdict(int)
    sec_caps = {s["id"]: s["capacity"] for s in inp["sections"].values()}

    # 单位级禁止索引：unit_forbidden[ui] = 与 ui 中任意成员互斥的其他单位集合
    unit_forbidden = [set() for _ in units]
    if use_rules:
        for a, b, _rid in inp["must_not_edges"]:
            ua, ub = pid_unit.get(a), pid_unit.get(b)
            if ua is not None and ub is not None and ua != ub:
                unit_forbidden[ua].add(ub)
                unit_forbidden[ub].add(ua)

    # 每张桌当前"有谁"（单位级），用于禁止判定
    table_units = [set() for _ in range(n_tables)]

    # 动态域（预置单位除外）
    dyn = {ui: list(domains[ui]) for ui in range(len(units)) if ui not in forced}
    unplaced = set(dyn.keys())
    # 桌 -> 静态候选域含该桌的单位（容量受害者只可能在其中）
    units_for_table = [set() for _ in range(n_tables)]
    for ui, dom in dyn.items():
        for cand in dom:
            units_for_table[table_idx[cand]].add(ui)
    # 每个单位的稳定随机平局键
    tie_key = {ui: rng.random() for ui in dyn}
    assign = {}
    state = {"nodes": 0}

    def can_place(ui, tid):
        """单位 ui 能否放入桌 tid（容量/分区/禁止关系）。"""
        ti = table_idx[tid]
        size = len(units[ui])
        if table_load[ti] + size > table_caps[ti]:
            return False
        sec = sec_of_table[ti]
        if sec_caps.get(sec) and section_load[sec] + size > sec_caps[sec]:
            return False
        if use_rules:
            seated = table_units[ti]
            for ou in unit_forbidden[ui]:
                if ou in seated:
                    return False
        return True

    def place(ui, tid):
        ti = table_idx[tid]
        table_load[ti] += len(units[ui])
        table_members[ti].update(units[ui])
        table_units[ti].add(ui)
        section_load[sec_of_table[ti]] += len(units[ui])
        for p in units[ui]:
            assign[p] = tid

    def remove(ui, tid):
        ti = table_idx[tid]
        section_load[sec_of_table[ti]] -= len(units[ui])
        table_units[ti].discard(ui)
        table_members[ti].difference_update(units[ui])
        table_load[ti] -= len(units[ui])
        for p in units[ui]:
            assign.pop(p, None)

    # MRV 用懒删除堆：键为 (域大小, -单位人数, 随机键, 代数, ui)
    import heapq

    heap = []
    heap_gen = {ui: 0 for ui in dyn}

    def push_mrv(ui):
        heapq.heappush(
            heap, (len(dyn[ui]), -len(units[ui]), tie_key[ui], heap_gen[ui], ui))

    for ui in dyn:
        push_mrv(ui)

    def bump_mrv(ui):
        heap_gen[ui] += 1
        push_mrv(ui)

    def prune_all():
        """初始化传播：预置单位全部落位后，对全部未放置单位裁剪动态域。"""
        snapshot = []
        for o in list(unplaced):
            osize = len(units[o])
            kept, dropped = [], []
            forbids = unit_forbidden[o] if use_rules else ()
            for cand in dyn[o]:
                ci = table_idx[cand]
                bad = table_load[ci] + osize > table_caps[ci]
                if not bad and sec_caps.get(sec_of_table[ci]):
                    bad = section_load[sec_of_table[ci]] + osize > sec_caps[sec_of_table[ci]]
                if not bad and use_rules and table_units[ci] & forbids:
                    bad = True
                (dropped if bad else kept).append(cand)
            if dropped:
                snapshot.append((o, dropped))
                dyn[o] = kept
                bump_mrv(o)
            if not kept:
                return snapshot, o
        return snapshot, None

    def prune_for(placed_ui, tid):
        """递归中放置 (placed_ui, tid) 后的增量传播。

        本次放置只改变了桌 tid 的负载与其上单位集合，因此：
        - 容量/分区受害者：候选含 tid 的单位，只需尝试从其域中删 tid；
        - 禁止受害者：与 placed_ui 相邻的单位，同样只需判定 tid。
        """
        ti = table_idx[tid]
        snapshot = []

        def try_remove(o):
            if tid not in dyn[o]:
                return None
            osize = len(units[o])
            bad = table_load[ti] + osize > table_caps[ti]
            if not bad and sec_caps.get(sec_of_table[ti]):
                bad = section_load[sec_of_table[ti]] + osize > sec_caps[sec_of_table[ti]]
            if not bad and use_rules and table_units[ti] & unit_forbidden[o]:
                bad = True
            if bad:
                dyn[o].remove(tid)
                snapshot.append((o, [tid]))
                bump_mrv(o)
                if not dyn[o]:
                    return o
            return None

        # 容量/分区受害者
        for o in units_for_table[ti]:
            if o in unplaced:
                empty = try_remove(o)
                if empty is not None:
                    return snapshot, empty
        # 禁止关系受害者（其域可能不含 tid，try_remove 内会跳过）
        if use_rules:
            for o in unit_forbidden[placed_ui]:
                if o in unplaced:
                    empty = try_remove(o)
                    if empty is not None:
                        return snapshot, empty
        return snapshot, None

    def restore(snapshot):
        for o, dropped in reversed(snapshot):
            dyn[o].extend(dropped)
            bump_mrv(o)

    # 预置单位依次落位（只检查容量/合法性），完成后做一次全量传播
    for ui in forced_order:
        tid = forced[ui]
        if not can_place(ui, tid):
            return None, 0, False
        place(ui, tid)
    _snap, empty = prune_all()
    if empty is not None:
        return None, 0, False

    total_people = len(inp["pid_set"])
    if total_people > sum(table_caps):
        return None, 0, False

    def pick_mrv():
        while heap:
            dlen, _neg, _tk, gen, ui = heap[0]
            if (ui not in unplaced or gen != heap_gen[ui]
                    or dlen != len(dyn[ui])):
                heapq.heappop(heap)
                continue
            return heapq.heappop(heap)[4]
        return -1

    def recurse():
        if state["nodes"] >= node_limit:
            return False
        if deadline is not None and time.monotonic() > deadline:
            return False
        if not unplaced:
            return True

        remaining = sum(len(units[ui]) for ui in unplaced)
        if remaining > sum(c - table_load[i] for i, c in enumerate(table_caps)):
            return False

        ui = pick_mrv()
        if ui == -1:
            return False
        vals = sorted(dyn[ui],
                      key=lambda t: (table_load[table_idx[t]], rng.random()))
        unplaced.discard(ui)
        for tid in vals:
            state["nodes"] += 1
            if state["nodes"] >= node_limit:
                unplaced.add(ui)
                bump_mrv(ui)
                return False
            if not can_place(ui, tid):
                continue
            place(ui, tid)
            snapshot, empty = prune_for(ui, tid)
            if empty is None and recurse():
                return True
            restore(snapshot)
            remove(ui, tid)
        unplaced.add(ui)
        bump_mrv(ui)
        return False

    ok = recurse()
    timed_out = state["nodes"] >= node_limit or (
        deadline is not None and time.monotonic() > deadline)
    if not ok:
        return None, state["nodes"], timed_out
    # 预置单位也要写入结果
    for ui, tid in forced.items():
        for p in units[ui]:
            assign[p] = tid
    return assign, state["nodes"], False


# ---------------------------------------------------------------- 无解诊断

def _baseline_capacity_diagnosis(inp: dict, units, pid_unit):
    """无任何编排规则（保留锁定/固定/容量）时不可行 => 容量与锁定层面的解释。"""
    detail = []
    tinfo = {t["id"]: t for t in inp["tables"]}

    # 锁定人数超过桌容量
    lock_count = defaultdict(int)
    for pid in inp["locked_pids"]:
        tid = inp["table_of"].get(pid)
        if tid is not None:
            lock_count[tid] += 1
    for pid, tid in inp["fixed_pid"].items():
        # 固定规则也算预置（不与锁定重复计数）
        if pid not in inp["locked_pids"]:
            lock_count[tid] += 1
    for tid, n in lock_count.items():
        t = tinfo.get(tid)
        if t and n > t["capacity"]:
            detail.append(
                f"桌位「{t['name']}」被固定/锁定了 {n} 人，但容量只有 {t['capacity']} 人"
            )

    # 分区超载
    sec_preset = defaultdict(int)
    for pid, tid in inp["table_of"].items():
        t = tinfo.get(tid)
        if t:
            sec_preset[t.get("section_id")] += 1
    for sec in inp["sections"].values():
        if sec["capacity"] and sec_preset[sec["id"]] > sec["capacity"]:
            detail.append(
                f"分区「{sec['name']}」已固定/锁定 {sec_preset[sec['id']]} 人，"
                f"超过分区容量 {sec['capacity']}"
            )

    total_people = len(inp["pid_set"])
    total_cap = sum(t["capacity"] for t in inp["tables"])
    if total_people > total_cap:
        detail.append(f"参与者共 {total_people} 人，但全部桌位总容量只有 {total_cap} 个座位")

    # 各分区：必须放入的人（其他分区装不下）——仅做软提示
    if not detail:
        detail.append("在忽略所有必须同桌/禁止同桌规则后仍然无法落座，问题出在锁定席位与容量安排上")
    return detail


def _diagnose(inp: dict, units, pid_unit, units_for_rules, seed: int):
    """删除式过滤（deletion-based MUS）：得到不可约的致冲突规则集合。

    从全部编排规则出发逐条尝试删除：删掉后可解 => 该规则必不可少，保留；
    删掉后仍无解 => 与本次无解无关（被其他矛盾支配），永久剔除。
    锁定席位与容量不参与删除，始终作为背景约束。
    """
    rules_sorted = sorted(inp["rules"], key=lambda r: r["id"])
    candidate = [r["id"] for r in rules_sorted]
    deadline = time.monotonic() + 15.0
    per_attempt_limit = 200_000

    def attempt(keep_ids):
        """仅保留 keep_ids 中的编排规则时是否可解：True/False；超时返回 None。"""
        sub_rules = [r for r in inp["rules"] if r["id"] in keep_ids]
        sinp = _build_inputs({
            "participants": inp["participants"],
            "tables": inp["tables"],
            "sections": list(inp["sections"].values()),
            "rules": sub_rules,
            "locks": {p: t for p, t in inp["table_of"].items() if p in inp["locked_pids"]},
        })
        sunits, spid_unit = _build_units(sinp)
        if _structural_checks(sinp, sunits, spid_unit):
            return False
        assign, _n, to = _search(
            sinp, sunits, spid_unit, use_rules=True, seed=seed,
            node_limit=per_attempt_limit, deadline=min(deadline, time.monotonic() + 2.0))
        if assign is not None:
            return True
        return None if to else False

    core = set(candidate)
    timed_out = False
    for rid in candidate:
        if time.monotonic() > deadline:
            timed_out = True
            break
        result = attempt(core - {rid})
        if result is False:
            core.discard(rid)   # 删掉仍无解 => rid 非必要
        elif result is None:
            timed_out = True    # 无法判定 => 保守保留
            break
        # result is True => rid 是核心成员，保留

    rules_by_id = {r["id"]: r for r in inp["rules"]}
    pname = {p["id"]: p["name"] for p in inp["participants"]}
    tname = {t["id"]: t["name"] for t in inp["tables"]}
    items = []
    for rid in sorted(core):
        r = rules_by_id.get(rid)
        if not r:
            continue
        items.append({"rule_id": rid, "description": describe_rule(r, pname, tname)})
    return items, timed_out


def describe_rule(rule: dict, pname: dict, tname: dict) -> str:
    """生成规则的人类可读描述（API 与前端共用同一份文案）。"""
    kind = rule["rule_type"]
    names = [pname.get(p, f"#{p}") for p in rule.get("pids", [])]
    if kind == "must_together":
        return "必须同桌：" + "、".join(names)
    if kind == "must_not":
        return "禁止同桌：" + "、".join(names)
    if kind == "fixed":
        return (
            f"固定席位：{pname.get(rule.get('pid'), '#' + str(rule.get('pid')))}"
            f" → 桌位「{tname.get(rule.get('tid'), '#' + str(rule.get('tid')))}」"
        )
    return f"规则 #{rule.get('id')}"


# ---------------------------------------------------------------- 对外入口

def analyze(data: dict) -> dict:
    """主入口。

    data:
      participants: [{id, name}]
      tables:       [{id, name, capacity, section_id}]
      sections:     [{id, name, capacity}]   capacity=0 表示不限
      rules:        [{id, rule_type, pids?, pid?, tid?, note?}]
      locks:        {pid: tid}               用户锁定的席位
      seed:         int
    返回:
      {feasible, assignment:{pid:tid}, nodes, conflicts:[{reason,rule_ids,description?}],
       involved_rules:[{rule_id, description}]}
    """
    seed = int(data.get("seed", 0))
    inp = _build_inputs(data)
    units, pid_unit = _build_units(inp)

    # 每条必须同桌规则贡献给了哪个单位（诊断文案用）
    units_for_rules = defaultdict(list)
    for g in inp["must_together"]:
        if g["members"]:
            ui = pid_unit.get(g["members"][0])
            if ui is not None:
                units_for_rules[ui].append(g["id"])

    pname = {p["id"]: p["name"] for p in inp["participants"]}
    tname = {t["id"]: t["name"] for t in inp["tables"]}

    # 1) 结构矛盾
    conflicts = _structural_checks(inp, units, pid_unit, units_for_rules)
    if conflicts:
        for c in conflicts:
            c["involved_rules"] = [
                {"rule_id": rid,
                 "description": describe_rule(
                     next((r for r in inp["rules"] if r["id"] == rid),
                          {"id": rid, "rule_type": "", "pids": [], "pid": None, "tid": None}),
                     pname, tname)}
                for rid in c.get("rule_ids", [])
            ]
        return {"feasible": False, "assignment": None, "nodes": 0,
                "conflicts": conflicts, "timed_out": False}

    # 2) 基线：只有容量 + 用户锁定（忽略全部编排规则，含固定席位）。
    #    基线不可行 => 是容量/锁定层面的问题；基线可行但完整搜索失败 => 规则冲突。
    base_inp = _build_inputs({
        "participants": inp["participants"],
        "tables": inp["tables"],
        "sections": list(inp["sections"].values()),
        "rules": [],
        "locks": {p: t for p, t in inp["table_of"].items() if p in inp["locked_pids"]},
    }, include_fixed=False)
    base_units, base_pid_unit = _build_units(base_inp)
    base_assign, _bn, bto = _search(
        base_inp, base_units, base_pid_unit, use_rules=False, seed=seed,
        node_limit=500_000, deadline=None)
    if base_assign is None:
        details = _baseline_capacity_diagnosis(inp, units, pid_unit)
        return {
            "feasible": False, "assignment": None, "nodes": 0,
            "conflicts": [{"reason": d, "rule_ids": [], "involved_rules": []}
                          for d in details],
            "timed_out": False,
        }

    # 3) 完整约束搜索
    assign, nodes, timed_out = _search(
        inp, units, pid_unit, use_rules=True, seed=seed,
        node_limit=500_000, deadline=time.monotonic() + 30.0)
    if assign is not None:
        return {"feasible": True, "assignment": assign, "nodes": nodes,
                "conflicts": [], "timed_out": False}

    # 4) 无解诊断：定位具体规则
    involved, diag_timeout = _diagnose(inp, units, pid_unit, units_for_rules, seed)
    if involved:
        lead = ("以下规则相互冲突（或与已锁定席位/容量冲突），"
                "放宽、删除其中任意一条后才可能出现可行安排：")
        reason = lead + "；".join(it["description"] for it in involved)
        conflicts = [{
            "reason": reason,
            "rule_ids": [it["rule_id"] for it in involved],
            "involved_rules": involved,
        }]
    else:
        conflicts = [{
            "reason": "搜索未找到可行安排，但未能自动定位到具体规则，请检查必须同桌组规模与禁止同桌组合",
            "rule_ids": [],
            "involved_rules": [],
        }]
    return {"feasible": False, "assignment": None, "nodes": nodes,
            "conflicts": conflicts, "timed_out": timed_out or diag_timeout}
