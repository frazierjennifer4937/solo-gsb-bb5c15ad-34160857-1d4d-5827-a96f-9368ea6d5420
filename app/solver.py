"""约束满足（CSP）求解器。

变量：每位参与者；值域：所有席位 (table_id, seat_no)。
约束：
  * 每个席位最多一人（占用位集判定，不裁剪值域）
  * 桌位容量（席位数量天然构成上限）
  * 分区容量上限（可选标签约束，也用于无解诊断）
  * together：同组所有人必须同桌
  * separate：同组所有人两两不同桌
  * fixed：单人/多人固定到某桌（可精确到席位号）
  * locked：用户在界面上锁定的既有安排（重新求解时作为固定值）

实现要点：
  * 值域用 Python 大整数位集表示，按桌分块：第 t 桌占据
    [offset[t], offset[t]+capacity[t]) 个 bit，位运算裁剪/计数 O(T/字长)。
  * 搜索采用 MRV（最小剩余值域）+ 前向检查的回溯算法。
  * 随机种子相同时结果完全一致（可重复）。
  * 无解时用"删除法"求一个极小冲突标签集合，指出具体是哪些规则 /
    锁定 / 分区 / 容量约束共同导致无解。
"""

from __future__ import annotations

import random
import sys
from dataclasses import dataclass, field

# 搜索深度等于参与者人数，放宽递归上限
sys.setrecursionlimit(20_000)

FAIL = "fail"
BUDGET = "budget"
OK = "ok"


@dataclass(frozen=True)
class PSpec:
    id: int
    name: str


@dataclass(frozen=True)
class TSpec:
    id: int
    name: str
    capacity: int
    section_id: int | None


@dataclass(frozen=True)
class RuleSpec:
    id: int
    type: str  # together / separate / fixed
    participant_ids: tuple[int, ...]
    table_id: int | None
    seat_no: int | None


@dataclass
class SolveResult:
    status: str  # ok / fail / budget
    assignments: dict[int, tuple[int, int]] = field(default_factory=dict)
    # 极小冲突标签：("rule", id) / ("lock", pid) / ("section", id) / ("capacity", None)
    conflicts: list[tuple[str, int | None]] = field(default_factory=list)
    nodes: int = 0


class _BudgetExceeded(Exception):
    pass


class Solver:
    def __init__(
        self,
        participants: list[PSpec],
        tables: list[TSpec],
        section_caps: dict[int, int],
        rules: list[RuleSpec],
        locks: dict[int, tuple[int, int]],
        seed: int = 0,
        node_limit: int = 200_000,
    ):
        self.ps = sorted(participants, key=lambda p: p.id)
        self.tables = sorted(tables, key=lambda t: t.id)
        self.section_caps = dict(section_caps)
        self.rules = sorted(rules, key=lambda r: r.id)
        self.locks = dict(locks)
        self.seed = seed
        self.node_limit = node_limit

        # ---- 席位位集布局 ----
        # 第 i 个 bit 对应第 i 个席位，按桌顺序紧密排列
        self.offset: dict[int, int] = {}
        self.table_bits: dict[int, int] = {}      # 本桌席位位掩码
        self.all_bits = 0
        self.total_seats = 0
        for t in self.tables:
            self.offset[t.id] = self.total_seats
            bits = ((1 << t.capacity) - 1) << self.total_seats
            self.table_bits[t.id] = bits
            self.all_bits |= bits
            self.total_seats += t.capacity
        # bit -> (table_id, seat_no)
        self.bit_table: list[int] = []
        self.bit_seat: list[int] = []
        self.seat_bit: dict[tuple[int, int], int] = {}
        for t in self.tables:
            for no in range(1, t.capacity + 1):
                bit = 1 << (self.offset[t.id] + no - 1)
                self.bit_table.append(t.id)
                self.bit_seat.append(no)
                self.seat_bit[(t.id, no)] = bit

        self.table_cap = {t.id: t.capacity for t in self.tables}
        self.table_section = {t.id: t.section_id for t in self.tables}
        # 桌顺序（确定性）
        self.table_ids = [t.id for t in self.tables]

        self.pids = [p.id for p in self.ps]
        self.valid_pids = set(self.pids)

        self.all_labels: list[tuple[str, int | None]] = (
            [("rule", r.id) for r in self.rules]
            + [("lock", pid) for pid in sorted(self.locks)]
            + [("section", sid) for sid in sorted(self.section_caps)]
            + [("capacity", None)]
        )

    # ------------------------------------------------------------------
    def solve_full(self) -> SolveResult:
        result = self._run(set(self.all_labels))
        if result.status == OK:
            return result
        if result.status == BUDGET:
            return result

        # 删除法求极小冲突核心
        core = set(self.all_labels)
        changed = True
        while changed:
            changed = False
            for label in list(core):
                sub = self._run(core - {label})
                if sub.status == FAIL:
                    core.discard(label)
                    changed = True
                # budget 时保守保留该标签
        if not core:
            core = {("capacity", None)}
        return SolveResult(status=FAIL, conflicts=sorted(core, key=str), nodes=result.nodes)

    # ------------------------------------------------------------------
    def _run(self, active_labels: set[tuple[str, int | None]]) -> SolveResult:
        active_rules = {rid for k, rid in active_labels if k == "rule"}
        active_sections = {sid for k, sid in active_labels if k == "section"}
        active_locks = {
            pid: seat for pid, seat in self.locks.items() if ("lock", pid) in active_labels
        }

        if len(self.pids) > self.total_seats and ("capacity", None) in active_labels:
            return SolveResult(status=FAIL)

        # 静态域（fixed 规则 + 锁定收窄）
        domains: dict[int, int] = {pid: self.all_bits for pid in self.pids}

        # ---- together 传递闭包：A~B、B~C => A、B、C 必须同桌 ----
        # 用并查集把所有 together 规则合并成互不相交的"同桌分量"。
        parent = {pid: pid for pid in self.pids}

        def uf_find(x: int) -> int:
            root = x
            while parent[root] != root:
                root = parent[root]
            while parent[x] != root:
                parent[x], x = root, parent[x]
            return root

        def uf_union(a: int, b: int) -> None:
            parent[uf_find(a)] = uf_find(b)

        for r in self.rules:
            if r.id in active_rules and r.type == "together":
                members = [pid for pid in r.participant_ids if pid in self.valid_pids]
                if len(members) >= 2:
                    for q in members[1:]:
                        uf_union(members[0], q)

        comp_members: dict[int, set[int]] = {}
        for pid in self.pids:
            comp_members.setdefault(uf_find(pid), set()).add(pid)

        # 每个 pid 所属的 together 分量（仅保留 >1 人的分量）
        pid_comp: dict[int, int] = {}
        comps: dict[int, set[int]] = {}
        for root, members in comp_members.items():
            if len(members) > 1:
                comps[root] = members
                for q in members:
                    pid_comp[q] = root

        # 分量容量预检：任何同桌分量都必须能装进至少一张桌；
        # 同时记录其合法桌集合（容量足够的桌）。
        comp_tables: dict[int, set[int]] = {}
        # pid -> 涉及的 together 规则 id（用于容量不可行时的冲突诊断）
        pid_rule_ids: dict[int, list[int]] = {}
        for r in self.rules:
            if r.id in active_rules and r.type == "together":
                for q in r.participant_ids:
                    if q in self.valid_pids:
                        pid_rule_ids.setdefault(q, []).append(r.id)
        for cid, members in comps.items():
            size = len(members)
            feasible_tables = {
                tid for tid in self.table_ids if self.table_cap[tid] >= size
            }
            if not feasible_tables:
                # 该分量装不进任何桌 => 无解，冲突涉及构成它的所有 together 规则
                rule_ids = {
                    rid
                    for q in members
                    for rid in pid_rule_ids.get(q, [])
                }
                return SolveResult(
                    status=FAIL,
                    conflicts=[("rule", rid) for rid in sorted(rule_ids)],
                )
            comp_tables[cid] = feasible_tables

        # separate 规则索引（保留原结构用于传播）
        separate_of: dict[int, list[int]] = {}
        sep_groups: dict[int, set[int]] = {}
        for r in self.rules:
            if r.id in active_rules and r.type == "separate":
                members = set(r.participant_ids) & self.valid_pids
                if not members:
                    continue
                sep_groups[r.id] = members
                for pid in members:
                    separate_of.setdefault(pid, []).append(r.id)

        # fixed 收窄
        fixed_for: dict[int, list[RuleSpec]] = {}
        for r in self.rules:
            if r.id in active_rules and r.type == "fixed":
                for pid in set(r.participant_ids) & self.valid_pids:
                    fixed_for.setdefault(pid, []).append(r)
        for pid, rs in fixed_for.items():
            for r in rs:
                allowed = self.table_bits.get(r.table_id, 0)
                if r.seat_no is not None:
                    allowed = self.seat_bit.get((r.table_id, r.seat_no), 0)
                domains[pid] &= allowed
                if domains[pid] == 0:
                    return SolveResult(status=FAIL)

        # 锁定收窄为单 bit
        for pid, (tid, no) in active_locks.items():
            if pid not in domains:
                continue
            bit = self.seat_bit.get((tid, no))
            if bit is None or not (domains[pid] & bit):
                return SolveResult(status=FAIL)
            domains[pid] = bit

        # ---- 搜索状态 ----
        occupied = 0                    # 已占用席位列
        assignment_bit: dict[int, int] = {}  # pid -> bit
        table_used: dict[int, int] = {tid: 0 for tid in self.table_ids}
        section_counts: dict[int, int] = {sid: 0 for sid in active_sections}
        # together 分量：cid -> 已选定的桌（一旦分量内第一人入座即确定）
        comp_table: dict[int, int] = {}
        # separate 规则：gid -> {tid: 已入座人数}
        separate_state: dict[int, dict[int, int]] = {}
        # 动态裁剪：pid -> 被规则传播删除的 bit
        removed: dict[int, int] = {pid: 0 for pid in self.pids}
        # 尚未入座的参与者（随 place/rollback 增量维护）
        unassigned: set[int] = set(self.pids)
        # 剩余空座掩码与计数（增量维护，避免每节点对大整数 bit_count）
        free_mask = self.all_bits
        free_seats = self.total_seats

        nodes = 0
        # 注意：fixed 参与者的域已被收窄，必须走精确 MRV，不能进入"普通快速路径"，
        # 否则其固定席位/桌可能被他人先占走。
        constrained: set[int] = (
            set(pid_comp) | set(separate_of) | set(fixed_for)
        )
        free_only: set[int] = set(self.pids) - constrained
        # 普通（无规则约束）未入座者的最小 id 指针；free_sorted 按 id 排序。
        # 入座只会让指针前进；回滚加入更小 id 时把指针回退到其位置。
        free_sorted = sorted(set(self.pids) - constrained)
        free_index = {pid: i for i, pid in enumerate(free_sorted)}
        free_ptr = [0]

        def min_free() -> int | None:
            while free_ptr[0] < len(free_sorted):
                pid = free_sorted[free_ptr[0]]
                if pid in unassigned:
                    return pid
                free_ptr[0] += 1
            return None

        def rollback(changes: list) -> None:
            nonlocal occupied, free_mask, free_seats
            for kind, *a in reversed(changes):
                if kind == "seat":
                    occupied &= ~a[0]
                    free_mask |= a[0]
                    free_seats += 1
                elif kind == "unassign":
                    pid = a[0]
                    unassigned.add(pid)
                    fi = free_index.get(pid)
                    if fi is not None and fi < free_ptr[0]:
                        free_ptr[0] = fi
                elif kind == "table":
                    table_used[a[0]] -= 1
                elif kind == "section":
                    section_counts[a[0]] -= 1
                elif kind == "remove":
                    removed[a[0]] &= ~a[1]
                elif kind == "comp":
                    comp_table.pop(a[0], None)
                elif kind == "sep":
                    gid, tid = a
                    n = separate_state[gid].get(tid, 0) - 1
                    if n <= 0:
                        separate_state[gid].pop(tid, None)
                    else:
                        separate_state[gid][tid] = n

        def place(pid: int, bit: int) -> list | None:
            nonlocal occupied, free_mask, free_seats
            idx = bit.bit_length() - 1
            tid = self.bit_table[idx]
            sid = self.table_section.get(tid)
            changes = [("seat", bit), ("unassign", pid), ("table", tid)]
            occupied |= bit
            free_mask &= ~bit
            free_seats -= 1
            assignment_bit[pid] = bit
            unassigned.discard(pid)
            table_used[tid] += 1
            if sid is not None and sid in active_sections:
                section_counts[sid] += 1
                changes.append(("section", sid))
                if section_counts[sid] > self.section_caps[sid]:
                    rollback(changes)
                    return None

            pending: dict[int, int] = {}

            def queue(q: int, mask: int) -> None:
                pending[q] = pending.get(q, 0) | mask

            # ---- together 分量 ----
            cid = pid_comp.get(pid)
            if cid is not None:
                members = comps[cid]
                chosen = comp_table.get(cid)
                if chosen is None:
                    if tid not in comp_tables[cid]:
                        rollback(changes)
                        return None
                    comp_table[cid] = tid
                    changes.append(("comp", cid))
                elif chosen != tid:
                    rollback(changes)
                    return None

                # 分量剩余成员必须装得进本桌当前空位
                placed = sum(
                    1 for q in members if q in assignment_bit
                )  # 含当前人
                need = len(members) - placed
                if need > self.table_cap[tid] - table_used[tid]:
                    rollback(changes)
                    return None

                # 其他成员只能选本桌
                forbid = self.all_bits & ~self.table_bits[tid]
                for q in members:
                    if q not in assignment_bit:
                        queue(q, forbid)

            # ---- separate ----
            for gid in separate_of.get(pid, ()):
                st = separate_state.setdefault(gid, {})
                if st.get(tid, 0) > 0:
                    rollback(changes)
                    return None
                st[tid] = st.get(tid, 0) + 1
                changes.append(("sep", gid, tid))
                for q in sep_groups[gid]:
                    if q != pid and q not in assignment_bit:
                        queue(q, self.table_bits[tid])

            # 提交前检查不裁空任何人
            for q, mask in pending.items():
                if domains[q] & ~(removed[q] | mask | occupied) == 0:
                    rollback(changes)
                    return None
            for q, mask in pending.items():
                new_bits = mask & ~removed[q]
                if new_bits:
                    removed[q] |= new_bits
                    changes.append(("remove", q, new_bits))
            return changes

        def global_feasible() -> bool:
            """全局剪枝：未安排人数不能超过剩余空座；分区不超限。"""
            if len(unassigned) > free_seats:
                return False
            for sid, cnt in section_counts.items():
                if cnt > self.section_caps[sid]:
                    return False
            return True

        def dfs() -> bool:
            nonlocal nodes
            nodes += 1
            if nodes > self.node_limit:
                raise _BudgetExceeded

            if not global_feasible():
                return False

            free = free_mask
            free_count = free_seats

            # 已确定桌的 together 分量，其未入座成员优先（域只剩一张桌）
            urgent: set[int] = set()
            for cid, tid in comp_table.items():
                for q in comps[cid]:
                    if q not in assignment_bit:
                        urgent.add(q)

            # 只遍历受限且未入座者；普通参与者用 O(1) 代表
            choice: int | None = None
            best: tuple[int, int, int] | None = None
            for pid in constrained:
                if pid not in unassigned:
                    continue
                d = domains[pid] & ~removed[pid] & free
                cnt = d.bit_count()
                if cnt == 0:
                    return False
                key = (0 if pid in urgent else 1, cnt, pid)
                if best is None or key < best:
                    best = key
                    choice = pid

            generic_rep = min_free()
            if generic_rep is not None:
                key = (2, free_count, generic_rep)
                if best is None or key < best:
                    best = key
                    choice = generic_rep
            if choice is None:
                return True
            if best[1] == 0:
                return False

            avail = domains[choice] & ~removed[choice] & free

            # 快速路径：无任何规则约束的普通参与者，其任意合法值都可行，
            # 无需枚举全部空座（避免大规模下的 O(n²)）。
            if choice in free_only:
                # 优先当前最满的桌；桌内确定性地选最低空席
                # （seed 时对"桌序列"做可重复随机）
                cand_tables = [
                    tid for tid in self.table_ids
                    if self.table_bits[tid] & avail
                ]
                if self.seed:
                    rng = random.Random((self.seed << 20) ^ choice)
                    rng.shuffle(cand_tables)
                    bit_pick = None
                    if cand_tables:
                        tid_pick = rng.choice(cand_tables)
                        m = self.table_bits[tid_pick] & avail
                        bit_pick = m & -m
                else:
                    cand_tables.sort(key=lambda tid: (-table_used[tid], tid))
                    if not cand_tables:
                        return False
                    tid_pick = cand_tables[0]
                    m = self.table_bits[tid_pick] & avail
                    bit_pick = m & -m
                if bit_pick is not None:
                    changes = place(choice, bit_pick)
                    if changes is not None:
                        if dfs():
                            return True
                        rollback(changes)
                return False

            # 取值顺序：按桌分组，优先当前较满的桌（先紧后松）；桌内按席位号
            table_vals: dict[int, list[int]] = {}
            b = avail
            while b:
                low = b & -b
                idx = low.bit_length() - 1
                table_vals.setdefault(self.bit_table[idx], []).append(low)
                b ^= low
            # 若该参与者所属分量已确定桌，只能去那张桌
            forced_cid = pid_comp.get(choice)
            if forced_cid is not None and forced_cid in comp_table:
                forced_tid = comp_table[forced_cid]
                table_vals = {forced_tid: table_vals.get(forced_tid, [])}
            ordered_tables = sorted(
                table_vals,
                key=lambda tid: (-table_used[tid], tid),
            )
            bits: list[int] = []
            for tid in ordered_tables:
                bits.extend(sorted(table_vals[tid]))
            if self.seed:
                rng = random.Random((self.seed << 20) ^ choice)
                rng.shuffle(bits)

            for bit in bits:
                changes = place(choice, bit)
                if changes is not None:
                    if dfs():
                        return True
                    rollback(changes)
            return False

        # ---- 先放置锁定者 ----
        # 若同一 together 分量的锁定者锁到了不同桌，place 会立即失败
        for pid in sorted(active_locks):
            tid, no = active_locks[pid]
            bit = self.seat_bit.get((tid, no))
            if bit is None or occupied & bit:
                return SolveResult(status=FAIL)
            if place(pid, bit) is None:
                return SolveResult(status=FAIL)

        # 锁定者入座后，校验每个已确定桌的分量仍装得下
        for cid, members in comps.items():
            chosen = comp_table.get(cid)
            if chosen is not None and len(members) > self.table_cap[chosen]:
                return SolveResult(status=FAIL)

        try:
            found = dfs()
        except _BudgetExceeded:
            return SolveResult(status=BUDGET, nodes=nodes)
        if not found:
            return SolveResult(status=FAIL, nodes=nodes)

        out: dict[int, tuple[int, int]] = {}
        for pid, bit in assignment_bit.items():
            idx = bit.bit_length() - 1
            out[pid] = (self.bit_table[idx], self.bit_seat[idx])
        return SolveResult(status=OK, assignments=out, nodes=nodes)
