"""FastAPI 应用：参与者 / 分区 / 桌位 / 规则 / 席位编排 API 与静态前端。"""
from __future__ import annotations

import csv
import io
import json
from typing import Optional

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from . import solver
from .database import get_conn, init_db, reset_db

app = FastAPI(title="活动席位编排", version="1.0")


# ---------------------------------------------------------------- Pydantic

class SectionIn(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    capacity: int = Field(default=0, ge=0)


class TableIn(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    capacity: int = Field(gt=0, le=1000)
    section_id: Optional[int] = None


class ParticipantIn(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    note: str = Field(default="", max_length=200)


class RuleIn(BaseModel):
    rule_type: str
    pids: list[int] = Field(default_factory=list)
    pid: Optional[int] = None
    tid: Optional[int] = None
    note: str = Field(default="", max_length=200)

    @field_validator("rule_type")
    @classmethod
    def _type_ok(cls, v):
        if v not in ("must_together", "must_not", "fixed"):
            raise ValueError("rule_type 必须是 must_together / must_not / fixed")
        return v


class SolveIn(BaseModel):
    seed: int = Field(default=0)


class LockIn(BaseModel):
    participant_id: int
    table_id: Optional[int] = None  # None = 解锁（回到未安排）
    locked: bool = True


# ---------------------------------------------------------------- 序列化

def _sections(conn):
    return [dict(r) for r in conn.execute(
        "SELECT id, name, capacity FROM sections ORDER BY id")]


def _tables(conn):
    return [dict(r) for r in conn.execute(
        "SELECT id, name, capacity, section_id FROM tables ORDER BY id")]


def _participants(conn):
    return [dict(r) for r in conn.execute(
        "SELECT id, name, note FROM participants ORDER BY id")]


def _rules(conn):
    out = []
    for r in conn.execute("SELECT * FROM rules ORDER BY id"):
        d = dict(r)
        d["pids"] = json.loads(d["pids"] or "[]")
        out.append(d)
    return out


def _assignments(conn):
    return [dict(r) for r in conn.execute(
        """SELECT a.participant_id, a.table_id, a.locked
           FROM assignments a ORDER BY a.participant_id""")]


def _rule_payload(rule: RuleIn, conn) -> dict:
    """校验规则并返回入库字段。"""
    pids = [p for p in dict.fromkeys(rule.pids)]  # 去重保序
    pid, tid = rule.pid, rule.tid

    if rule.rule_type in ("must_together", "must_not"):
        if rule.rule_type == "must_together" and len(pids) < 2:
            raise HTTPException(422, "必须同桌规则至少选择 2 名参与者")
        if rule.rule_type == "must_not" and len(pids) < 2:
            raise HTTPException(422, "禁止同桌规则至少选择 2 名参与者")
        for p in pids:
            if not conn.execute("SELECT 1 FROM participants WHERE id=?", (p,)).fetchone():
                raise HTTPException(422, f"参与者 {p} 不存在")
        return {"rule_type": rule.rule_type, "pids": pids, "pid": None, "tid": None,
                "note": rule.note}

    # fixed
    if pid is None or tid is None:
        raise HTTPException(422, "固定席位规则需要指定参与者与桌位")
    if not conn.execute("SELECT 1 FROM participants WHERE id=?", (pid,)).fetchone():
        raise HTTPException(422, "参与者不存在")
    if not conn.execute("SELECT 1 FROM tables WHERE id=?", (tid,)).fetchone():
        raise HTTPException(422, "桌位不存在")
    if conn.execute("SELECT id FROM rules WHERE rule_type='fixed' AND pid=?",
                    (pid,)).fetchone():
        raise HTTPException(422, "该参与者已有固定席位规则，请先删除旧规则")
    return {"rule_type": "fixed", "pids": [], "pid": pid, "tid": tid,
            "note": rule.note}


# ---------------------------------------------------------------- 状态

@app.get("/api/state")
def get_state():
    with get_conn() as conn:
        return {
            "sections": _sections(conn),
            "tables": _tables(conn),
            "participants": _participants(conn),
            "rules": _rules(conn),
            "assignments": _assignments(conn),
        }


# ---------------------------------------------------------------- 分区

@app.post("/api/sections", status_code=201)
def create_section(body: SectionIn):
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO sections(name, capacity) VALUES (?, ?)",
            (body.name, body.capacity))
        return {"id": cur.lastrowid, "name": body.name, "capacity": body.capacity}


@app.delete("/api/sections/{sid}", status_code=204)
def delete_section(sid: int):
    with get_conn() as conn:
        conn.execute("UPDATE tables SET section_id=NULL WHERE section_id=?", (sid,))
        conn.execute("DELETE FROM sections WHERE id=?", (sid,))
    return Response(status_code=204)


# ---------------------------------------------------------------- 桌位

@app.post("/api/tables", status_code=201)
def create_table(body: TableIn):
    with get_conn() as conn:
        if body.section_id is not None and not conn.execute(
                "SELECT 1 FROM sections WHERE id=?", (body.section_id,)).fetchone():
            raise HTTPException(422, "分区不存在")
        cur = conn.execute(
            "INSERT INTO tables(name, capacity, section_id) VALUES (?, ?, ?)",
            (body.name, body.capacity, body.section_id))
        return {"id": cur.lastrowid, **body.model_dump()}


@app.delete("/api/tables/{tid}", status_code=204)
def delete_table(tid: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM tables WHERE id=?", (tid,))
    return Response(status_code=204)


# ---------------------------------------------------------------- 参与者

@app.post("/api/participants", status_code=201)
def create_participant(body: ParticipantIn):
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO participants(name, note) VALUES (?, ?)",
            (body.name, body.note))
        return {"id": cur.lastrowid, "name": body.name, "note": body.note}


@app.delete("/api/participants/{pid}", status_code=204)
def delete_participant(pid: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM participants WHERE id=?", (pid,))
    return Response(status_code=204)


@app.post("/api/participants/import-csv")
async def import_participants_csv(file: UploadFile = File(...)):
    """CSV 列：name（必填）, note（可选）。表头中英文均可。"""
    raw = await file.read()
    text = None
    for enc in ("utf-8-sig", "utf-8", "gbk", "gb18030"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise HTTPException(422, "无法识别 CSV 文件编码，请使用 UTF-8 或 GBK")

    reader = csv.reader(io.StringIO(text))
    rows = [r for r in reader if any(c.strip() for c in r)]
    if not rows:
        raise HTTPException(422, "CSV 文件为空")

    header = [c.strip().lower() for c in rows[0]]
    name_aliases = {"name", "姓名", "名字", "参与者"}
    note_aliases = {"note", "备注", "说明"}
    if name_aliases & set(header):
        ni = next(i for i, h in enumerate(header) if h in name_aliases)
        mi = next((i for i, h in enumerate(header) if h in note_aliases), None)
        data = [(r[ni].strip(), (r[mi].strip() if mi is not None and mi < len(r) else ""))
                for r in rows[1:] if ni < len(r) and r[ni].strip()]
    elif header and header[0] in note_aliases:
        raise HTTPException(422, "CSV 表头需要包含 name（姓名）列")
    else:
        # 无表头：第一列即姓名（首行同样是数据）
        data = [(r[0].strip(), r[1].strip() if len(r) > 1 else "")
                for r in rows if r and r[0].strip()]

    if not data:
        raise HTTPException(422, "CSV 中没有可导入的参与者（需要 name/姓名 列）")

    with get_conn() as conn:
        conn.executemany(
            "INSERT INTO participants(name, note) VALUES (?, ?)", data)
    return {"imported": len(data)}


@app.get("/api/participants/template.csv")
def participant_template():
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["name", "note"])
    writer.writerow(["张三", "VIP"])
    writer.writerow(["李四", ""])
    return Response(
        "﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition":
                 "attachment; filename=participants_template.csv"})


# ---------------------------------------------------------------- 规则

@app.post("/api/rules", status_code=201)
def create_rule(body: RuleIn):
    with get_conn() as conn:
        payload = _rule_payload(body, conn)
        cur = conn.execute(
            """INSERT INTO rules(rule_type, pids, pid, tid, note)
               VALUES (?, ?, ?, ?, ?)""",
            (payload["rule_type"], json.dumps(payload["pids"]),
             payload["pid"], payload["tid"], payload["note"]))
        return {"id": cur.lastrowid, **payload, "note": body.note}


@app.delete("/api/rules/{rid}", status_code=204)
def delete_rule(rid: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM rules WHERE id=?", (rid,))
    return Response(status_code=204)


# ---------------------------------------------------------------- 求解

def _solver_input(conn, seed: int) -> dict:
    sections = _sections(conn)
    tables = _tables(conn)
    participants = _participants(conn)
    rules = _rules(conn)
    locks = {}
    for a in _assignments(conn):
        if a["locked"]:
            locks[a["participant_id"]] = a["table_id"]
    return {
        "sections": sections,
        "tables": tables,
        "participants": participants,
        "rules": rules,
        "locks": locks,
        "seed": seed,
    }


@app.post("/api/solve")
def solve(body: SolveIn):
    with get_conn() as conn:
        data = _solver_input(conn, body.seed)
        result = solver.analyze(data)

        if not result["feasible"]:
            return {
                "feasible": False,
                "seed": body.seed,
                "nodes": result["nodes"],
                "timed_out": result["timed_out"],
                "conflicts": result["conflicts"],
            }

        # 成功：未锁定的旧安排全部重排，锁定的保持不动
        locked_pids = set(data["locks"].keys())
        if locked_pids:
            conn.execute(
                "DELETE FROM assignments WHERE participant_id NOT IN ({})".format(
                    ",".join(str(p) for p in locked_pids)))
        else:
            conn.execute("DELETE FROM assignments")
        rows = [(pid, tid, 1 if pid in locked_pids else 0)
                for pid, tid in result["assignment"].items()]
        conn.executemany(
            """INSERT INTO assignments(participant_id, table_id, locked)
               VALUES (?, ?, ?)
               ON CONFLICT(participant_id) DO UPDATE SET table_id=excluded.table_id""",
            rows)
        return {
            "feasible": True,
            "seed": body.seed,
            "nodes": result["nodes"],
            "seated": len(rows),
            "locked": len(locked_pids),
            "conflicts": [],
        }


# ---------------------------------------------------------------- 锁定 / 清空 / 导出

@app.post("/api/assignments/lock")
def lock_assignment(body: LockIn):
    """手动设置某人的席位并锁定；table_id=None 时取消安排。"""
    with get_conn() as conn:
        if not conn.execute("SELECT 1 FROM participants WHERE id=?",
                            (body.participant_id,)).fetchone():
            raise HTTPException(422, "参与者不存在")
        if body.table_id is None:
            conn.execute("DELETE FROM assignments WHERE participant_id=?",
                         (body.participant_id,))
            return {"ok": True}
        if not conn.execute("SELECT 1 FROM tables WHERE id=?",
                            (body.table_id,)).fetchone():
            raise HTTPException(422, "桌位不存在")
        conn.execute(
            """INSERT INTO assignments(participant_id, table_id, locked)
               VALUES (?, ?, 1)
               ON CONFLICT(participant_id) DO UPDATE
               SET table_id=excluded.table_id, locked=1""",
            (body.participant_id, body.table_id))
        return {"ok": True}


@app.post("/api/assignments/unlock-one/{pid}")
def unlock_one(pid: int):
    """保留席位安排但取消锁定（下次求解时此人可被重排）。"""
    with get_conn() as conn:
        conn.execute("UPDATE assignments SET locked=0 WHERE participant_id=?", (pid,))
    return {"ok": True}


@app.post("/api/assignments/unlock-all")
def unlock_all():
    """全部解锁（安排保留，仍可再次求解时重排）。"""
    with get_conn() as conn:
        conn.execute("UPDATE assignments SET locked=0")
    return {"ok": True}


@app.post("/api/assignments/clear")
def clear_assignments():
    with get_conn() as conn:
        conn.execute("DELETE FROM assignments")
    return {"ok": True}


@app.get("/api/export.csv")
def export_csv():
    """导出当前编排结果（未安排者桌位/分区留空）。"""
    with get_conn() as conn:
        participants = _participants(conn)
        tables = {t["id"]: t for t in _tables(conn)}
        sections = {s["id"]: s for s in _sections(conn)}
        assigns = {a["participant_id"]: a for a in _assignments(conn)}

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["participant_id", "姓名", "备注", "分区", "桌位",
                     "桌位容量", "锁定"])
    for p in participants:
        a = assigns.get(p["id"])
        if a:
            t = tables.get(a["table_id"])
            s = sections.get(t["section_id"]) if t else None
            writer.writerow([
                p["id"], p["name"], p["note"],
                s["name"] if s else "",
                t["name"] if t else f"#{a['table_id']}",
                t["capacity"] if t else "",
                "是" if a["locked"] else "否",
            ])
        else:
            writer.writerow([p["id"], p["name"], p["note"], "", "未安排", "", ""])

    return Response(
        "﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=seating.csv"})


# ---------------------------------------------------------------- 演示数据

@app.post("/api/demo")
def load_demo():
    reset_db()
    with get_conn() as conn:
        sec_a = conn.execute(
            "INSERT INTO sections(name, capacity) VALUES (?, ?)",
            ("A区（贵宾）", 8)).lastrowid
        sec_b = conn.execute(
            "INSERT INTO sections(name, capacity) VALUES (?, ?)",
            ("B区", 0)).lastrowid
        t1 = conn.execute(
            "INSERT INTO tables(name, capacity, section_id) VALUES (?, ?, ?)",
            ("1号桌", 4, sec_a)).lastrowid
        for name, cap, sec in (("2号桌", 4, sec_a), ("3号桌", 6, sec_b),
                               ("4号桌", 6, sec_b)):
            conn.execute(
                "INSERT INTO tables(name, capacity, section_id) VALUES (?, ?, ?)",
                (name, cap, sec))

        names = ["张三", "李四", "王五", "赵六", "钱七", "孙八",
                 "周九", "吴十", "郑十一", "王十二", "冯十三", "陈十四",
                 "褚十五", "卫十六"]
        pids = []
        for n in names:
            pids.append(conn.execute(
                "INSERT INTO participants(name, note) VALUES (?, ?)",
                (n, "")).lastrowid)

        def rule(rt, pids_, pid=None, tid=None, note=""):
            conn.execute(
                """INSERT INTO rules(rule_type, pids, pid, tid, note)
                   VALUES (?, ?, ?, ?, ?)""",
                (rt, json.dumps(pids_), pid, tid, note))

        rule("must_together", [pids[0], pids[1], pids[2]], note="项目组同桌")
        rule("must_not", [pids[0], pids[3]], note="避免相邻")
        rule("must_not", [pids[4], pids[5]])
        rule("fixed", [], pid=pids[6], tid=t1, note="主持人位")

    return {"ok": True}


# ---------------------------------------------------------------- 前端静态资源

import os  # noqa: E402

_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")


@app.get("/", response_class=HTMLResponse)
def index():
    return FileResponse(os.path.join(_STATIC_DIR, "index.html"))


init_db()
