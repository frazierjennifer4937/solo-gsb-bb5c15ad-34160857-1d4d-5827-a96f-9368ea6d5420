"""FastAPI 入口：活动席位编排应用。"""

from __future__ import annotations

import csv
import io
import json
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app import models, schemas
from app.database import get_db, init_db
from app.solver import BUDGET, FAIL, PSpec, RuleSpec, Solver, TSpec


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="活动席位编排", version="1.0.0", lifespan=lifespan)


# ----------------------------------------------------------------------
# 序列化
# ----------------------------------------------------------------------
def serialize_rule(r: models.Rule) -> schemas.RuleOut:
    try:
        pids = json.loads(r.participant_ids or "[]")
    except json.JSONDecodeError:
        pids = []
    return schemas.RuleOut(
        id=r.id,
        type=r.type,
        participant_ids=pids,
        table_id=r.table_id,
        seat_no=r.seat_no,
        enabled=r.enabled,
        note=r.note,
    )


def serialize_state(db) -> dict:
    participants = db.scalars(select(models.Participant).order_by(models.Participant.id)).all()
    sections = db.scalars(select(models.Section).order_by(models.Section.id)).all()
    tables = db.scalars(
        select(models.Table).options(selectinload(models.Table.section)).order_by(models.Table.id)
    ).all()
    rules = db.scalars(select(models.Rule).order_by(models.Rule.id)).all()
    assignments = db.scalars(
        select(models.Assignment)
        .options(
            selectinload(models.Assignment.participant),
        )
        .order_by(models.Assignment.table_id, models.Assignment.seat_no)
    ).all()

    table_map = {t.id: t for t in tables}
    return {
        "participants": [
            {"id": p.id, "name": p.name, "group_name": p.group_name, "note": p.note}
            for p in participants
        ],
        "sections": [
            {"id": s.id, "name": s.name, "capacity": s.capacity} for s in sections
        ],
        "tables": [
            {
                "id": t.id,
                "name": t.name,
                "capacity": t.capacity,
                "section_id": t.section_id,
                "section_name": t.section.name if t.section else None,
            }
            for t in tables
        ],
        "rules": [serialize_rule(r).model_dump() for r in rules],
        "assignments": [
            {
                "id": a.id,
                "participant_id": a.participant_id,
                "participant_name": a.participant.name,
                "group_name": a.participant.group_name,
                "table_id": a.table_id,
                "table_name": table_map[a.table_id].name if a.table_id in table_map else None,
                "section_id": (
                    table_map[a.table_id].section_id if a.table_id in table_map else None
                ),
                "section_name": (
                    table_map[a.table_id].section.name
                    if a.table_id in table_map and table_map[a.table_id].section
                    else None
                ),
                "seat_no": a.seat_no,
                "locked": a.locked,
            }
            for a in assignments
        ],
    }


# ----------------------------------------------------------------------
# 状态与参与者
# ----------------------------------------------------------------------
@app.get("/api/state")
def get_state():
    db = next(get_db())
    try:
        return serialize_state(db)
    finally:
        db.close()


@app.post("/api/participants", response_model=schemas.ParticipantOut, status_code=201)
def create_participant(payload: schemas.ParticipantIn):
    db = next(get_db())
    try:
        p = models.Participant(
            name=payload.name.strip(),
            group_name=(payload.group_name or None),
            note=payload.note,
        )
        db.add(p)
        db.commit()
        db.refresh(p)
        return schemas.ParticipantOut(id=p.id, name=p.name, group_name=p.group_name, note=p.note)
    finally:
        db.close()


@app.put("/api/participants/{pid}", response_model=schemas.ParticipantOut)
def update_participant(pid: int, payload: schemas.ParticipantIn):
    db = next(get_db())
    try:
        p = db.get(models.Participant, pid)
        if not p:
            raise HTTPException(404, "参与者不存在")
        p.name = payload.name.strip()
        p.group_name = payload.group_name or None
        p.note = payload.note
        db.commit()
        return schemas.ParticipantOut(id=p.id, name=p.name, group_name=p.group_name, note=p.note)
    finally:
        db.close()


@app.delete("/api/participants/{pid}", status_code=204)
def delete_participant(pid: int):
    db = next(get_db())
    try:
        p = db.get(models.Participant, pid)
        if not p:
            raise HTTPException(404, "参与者不存在")
        # 从规则的 participant_ids JSON 中移除该人；清空后的规则一并删除
        rules = db.scalars(select(models.Rule)).all()
        for r in rules:
            try:
                ids = json.loads(r.participant_ids or "[]")
            except json.JSONDecodeError:
                ids = []
            if pid in ids:
                ids = [x for x in ids if x != pid]
                if len(ids) < 1 or (r.type in ("together", "separate") and len(ids) < 2):
                    db.delete(r)
                else:
                    r.participant_ids = json.dumps(ids)
        db.delete(p)
        db.commit()
        return Response(status_code=204)
    finally:
        db.close()


@app.post("/api/participants/import")
async def import_participants(file: UploadFile = File(...)):
    """CSV 导入。表头支持 name/group/note 及其中文别名；无表头时按 姓名,分组,备注 解析。"""
    raw = await file.read()
    for enc in ("utf-8-sig", "utf-8", "gbk"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise HTTPException(400, "无法识别文件编码，请使用 UTF-8 CSV")

    reader = csv.reader(io.StringIO(text))
    rows = [row for row in reader if any(c.strip() for c in row)]
    if not rows:
        raise HTTPException(400, "CSV 文件为空")

    header_aliases = {
        "name": {"name", "姓名", "名字", "参与者", "participant"},
        "group": {"group", "group_name", "分组", "组别", "团队"},
        "note": {"note", "备注", "说明", "memo"},
    }
    first = [c.strip().lower() for c in rows[0]]
    col_map: dict[str, int] = {}
    for field_name, aliases in header_aliases.items():
        for i, cell in enumerate(first):
            if cell in aliases:
                col_map[field_name] = i
    if "name" in col_map:
        data_rows = rows[1:]
    else:
        col_map = {"name": 0, "group": 1, "note": 2}
        data_rows = rows

    db = next(get_db())
    added = 0
    skipped = 0
    try:
        for row in data_rows:
            name = row[col_map["name"]].strip() if col_map["name"] < len(row) else ""
            if not name:
                skipped += 1
                continue
            group = None
            note = None
            if "group" in col_map and col_map["group"] < len(row):
                group = row[col_map["group"]].strip() or None
            if "note" in col_map and col_map["note"] < len(row):
                note = row[col_map["note"]].strip() or None
            db.add(models.Participant(name=name, group_name=group, note=note))
            added += 1
        db.commit()
        return {"added": added, "skipped": skipped}
    finally:
        db.close()


# ----------------------------------------------------------------------
# 分区与桌位
# ----------------------------------------------------------------------
@app.post("/api/sections", response_model=schemas.SectionOut, status_code=201)
def create_section(payload: schemas.SectionIn):
    db = next(get_db())
    try:
        s = models.Section(name=payload.name.strip(), capacity=payload.capacity)
        db.add(s)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(409, "分区名称已存在")
        db.refresh(s)
        return schemas.SectionOut(id=s.id, name=s.name, capacity=s.capacity)
    finally:
        db.close()


@app.put("/api/sections/{sid}", response_model=schemas.SectionOut)
def update_section(sid: int, payload: schemas.SectionIn):
    db = next(get_db())
    try:
        s = db.get(models.Section, sid)
        if not s:
            raise HTTPException(404, "分区不存在")
        s.name = payload.name.strip()
        s.capacity = payload.capacity
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(409, "分区名称已存在")
        return schemas.SectionOut(id=s.id, name=s.name, capacity=s.capacity)
    finally:
        db.close()


@app.delete("/api/sections/{sid}", status_code=204)
def delete_section(sid: int):
    db = next(get_db())
    try:
        s = db.get(models.Section, sid)
        if not s:
            raise HTTPException(404, "分区不存在")
        db.delete(s)
        db.commit()
        return Response(status_code=204)
    finally:
        db.close()


@app.post("/api/tables", response_model=schemas.TableOut, status_code=201)
def create_table(payload: schemas.TableIn):
    db = next(get_db())
    try:
        if payload.section_id is not None and not db.get(models.Section, payload.section_id):
            raise HTTPException(400, "指定的分区不存在")
        t = models.Table(
            name=payload.name.strip(),
            capacity=payload.capacity,
            section_id=payload.section_id,
        )
        db.add(t)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(409, "桌位名称已存在")
        db.refresh(t)
        return schemas.TableOut(
            id=t.id, name=t.name, capacity=t.capacity, section_id=t.section_id
        )
    finally:
        db.close()


@app.put("/api/tables/{tid}", response_model=schemas.TableOut)
def update_table(tid: int, payload: schemas.TableIn):
    db = next(get_db())
    try:
        t = db.get(models.Table, tid)
        if not t:
            raise HTTPException(404, "桌位不存在")
        if payload.section_id is not None and not db.get(models.Section, payload.section_id):
            raise HTTPException(400, "指定的分区不存在")
        # 缩容不能小于已安排的最大席位号
        used = db.scalar(
            select(models.Assignment.seat_no)
            .where(models.Assignment.table_id == tid)
            .order_by(models.Assignment.seat_no.desc())
            .limit(1)
        )
        if used is not None and used > payload.capacity:
            raise HTTPException(400, f"该桌已有席位号 {used} 的安排，容量不能小于 {used}")
        t.name = payload.name.strip()
        t.capacity = payload.capacity
        t.section_id = payload.section_id
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(409, "桌位名称已存在")
        return schemas.TableOut(
            id=t.id, name=t.name, capacity=t.capacity, section_id=t.section_id
        )
    finally:
        db.close()


@app.delete("/api/tables/{tid}", status_code=204)
def delete_table(tid: int):
    db = next(get_db())
    try:
        t = db.get(models.Table, tid)
        if not t:
            raise HTTPException(404, "桌位不存在")
        db.delete(t)  # 级联删除该桌安排与 fixed 规则
        db.commit()
        return Response(status_code=204)
    finally:
        db.close()


# ----------------------------------------------------------------------
# 规则
# ----------------------------------------------------------------------
def _validate_rule_payload(db, payload: schemas.RuleIn) -> None:
    pids = payload.participant_ids
    if len(pids) != len(set(pids)):
        raise HTTPException(400, "参与者列表有重复")
    existing = {
        p.id
        for p in db.scalars(select(models.Participant).where(models.Participant.id.in_(pids))).all()
    }
    missing = [x for x in pids if x not in existing]
    if missing:
        raise HTTPException(400, f"参与者不存在: {missing}")

    if payload.type in ("together", "separate"):
        if len(pids) < 2:
            raise HTTPException(400, f"{payload.type} 规则至少需要 2 名参与者")
        if payload.table_id is not None or payload.seat_no is not None:
            raise HTTPException(400, f"{payload.type} 规则不能指定桌位/席位")
    else:  # fixed
        if payload.table_id is None:
            raise HTTPException(400, "fixed 规则必须指定桌位")
        t = db.get(models.Table, payload.table_id)
        if not t:
            raise HTTPException(400, "指定的桌位不存在")
        if payload.seat_no is not None:
            if payload.seat_no > t.capacity:
                raise HTTPException(400, f"席位号超出该桌容量（{t.capacity}）")
            if len(pids) > 1:
                raise HTTPException(400, "精确到席位号的固定规则只能包含 1 名参与者")


def _rule_from_payload(r: models.Rule, payload: schemas.RuleIn) -> models.Rule:
    r.type = payload.type
    r.participant_ids = json.dumps(payload.participant_ids)
    r.table_id = payload.table_id
    r.seat_no = payload.seat_no
    r.enabled = payload.enabled
    r.note = payload.note
    return r


@app.post("/api/rules", response_model=schemas.RuleOut, status_code=201)
def create_rule(payload: schemas.RuleIn):
    db = next(get_db())
    try:
        _validate_rule_payload(db, payload)
        r = _rule_from_payload(models.Rule(), payload)
        db.add(r)
        db.commit()
        db.refresh(r)
        return serialize_rule(r)
    finally:
        db.close()


@app.put("/api/rules/{rid}", response_model=schemas.RuleOut)
def update_rule(rid: int, payload: schemas.RuleIn):
    db = next(get_db())
    try:
        r = db.get(models.Rule, rid)
        if not r:
            raise HTTPException(404, "规则不存在")
        _validate_rule_payload(db, payload)
        _rule_from_payload(r, payload)
        db.commit()
        return serialize_rule(r)
    finally:
        db.close()


@app.patch("/api/rules/{rid}", response_model=schemas.RuleOut)
def patch_rule(rid: int, payload: schemas.RuleUpdate):
    db = next(get_db())
    try:
        r = db.get(models.Rule, rid)
        if not r:
            raise HTTPException(404, "规则不存在")
        if payload.enabled is not None:
            r.enabled = payload.enabled
        if payload.note is not None:
            r.note = payload.note
        db.commit()
        return serialize_rule(r)
    finally:
        db.close()


@app.delete("/api/rules/{rid}", status_code=204)
def delete_rule(rid: int):
    db = next(get_db())
    try:
        r = db.get(models.Rule, rid)
        if not r:
            raise HTTPException(404, "规则不存在")
        db.delete(r)
        db.commit()
        return Response(status_code=204)
    finally:
        db.close()


# ----------------------------------------------------------------------
# 求解
# ----------------------------------------------------------------------
def _build_solver(db, payload: schemas.SolveIn) -> Solver:
    participants = [
        PSpec(id=p.id, name=p.name)
        for p in db.scalars(select(models.Participant).order_by(models.Participant.id)).all()
    ]
    tables = [
        TSpec(id=t.id, name=t.name, capacity=t.capacity, section_id=t.section_id)
        for t in db.scalars(select(models.Table).order_by(models.Table.id)).all()
    ]
    section_caps = {
        s.id: s.capacity
        for s in db.scalars(select(models.Section)).all()
        if s.capacity is not None
    }
    rules = []
    for r in db.scalars(select(models.Rule).where(models.Rule.enabled.is_(True))).all():
        try:
            pids = tuple(json.loads(r.participant_ids or "[]"))
        except json.JSONDecodeError:
            pids = ()
        rules.append(RuleSpec(r.id, r.type, pids, r.table_id, r.seat_no))
    locks = {
        a.participant_id: (a.table_id, a.seat_no)
        for a in db.scalars(
            select(models.Assignment).where(models.Assignment.locked.is_(True))
        ).all()
    }
    return Solver(
        participants=participants,
        tables=tables,
        section_caps=section_caps,
        rules=rules,
        locks=locks,
        seed=payload.seed,
        node_limit=payload.node_limit,
    )


def _describe_conflicts(db, conflicts: list[tuple[str, int | None]]) -> list[dict]:
    out = []
    p_cache: dict[int, str] = {}

    def pname(pid: int) -> str:
        if pid not in p_cache:
            p = db.get(models.Participant, pid)
            p_cache[pid] = p.name if p else f"#{pid}"
        return p_cache[pid]

    type_label = {"together": "必须同桌", "separate": "禁止同桌", "fixed": "固定席位"}

    for kind, key in conflicts:
        if kind == "rule" and key is not None:
            r = db.get(models.Rule, key)
            if r:
                names = [pname(pid) for pid in json.loads(r.participant_ids or "[]")]
                detail = f"规则 #{r.id}（{type_label.get(r.type, r.type)}）：{', '.join(names)}"
                t = db.get(models.Table, r.table_id) if r.table_id else None
                if t is not None:
                    detail += f" → {t.name}"
                    if r.seat_no is not None:
                        detail += f" 第 {r.seat_no} 席"
                if r.note:
                    detail += f"（{r.note}）"
                out.append({"kind": "rule", "id": key, "message": detail})
        elif kind == "lock" and key is not None:
            a = db.scalar(
                select(models.Assignment).where(models.Assignment.participant_id == key)
            )
            if a:
                t = db.get(models.Table, a.table_id)
                out.append(
                    {
                        "kind": "lock",
                        "id": key,
                        "message": (
                            f"锁定席位：{pname(key)} 已锁定到 {t.name if t else '#'+str(a.table_id)}"
                            f" 第 {a.seat_no} 席"
                        ),
                    }
                )
            else:
                out.append(
                    {"kind": "lock", "id": key, "message": f"锁定席位：{pname(key)}"}
                )
        elif kind == "section" and key is not None:
            s = db.get(models.Section, key)
            if s:
                out.append(
                    {
                        "kind": "section",
                        "id": key,
                        "message": f"分区容量：{s.name} 区上限 {s.capacity} 人",
                    }
                )
        elif kind == "capacity":
            out.append(
                {
                    "kind": "capacity",
                    "id": None,
                    "message": "总席位不足：参与者人数超过所有桌位的席位总数（或分区容量之和）",
                }
            )
    return out


@app.post("/api/solve")
def solve(payload: schemas.SolveIn):
    db = next(get_db())
    try:
        solver = _build_solver(db, payload)
        result = solver.solve_full()

        if result.status == BUDGET:
            raise HTTPException(
                409,
                f"搜索达到节点上限（{payload.node_limit}）仍无法确定解，请调整规则或提高上限",
            )

        if result.status == FAIL:
            return {
                "solvable": False,
                "seed": payload.seed,
                "nodes": result.nodes,
                "conflicts": _describe_conflicts(db, result.conflicts),
            }

        # 成功：保留锁定行，删除其余安排后写入新结果
        locked_ids = {
            a.participant_id
            for a in db.scalars(
                select(models.Assignment).where(models.Assignment.locked.is_(True))
            ).all()
        }
        for a in db.scalars(select(models.Assignment)).all():
            if not a.locked:
                db.delete(a)
        db.flush()
        for pid, (tid, seat_no) in result.assignments.items():
            if pid in locked_ids:
                continue
            db.add(
                models.Assignment(
                    participant_id=pid, table_id=tid, seat_no=seat_no, locked=False
                )
            )
        db.commit()
        return {
            "solvable": True,
            "seed": payload.seed,
            "nodes": result.nodes,
            "assignments": serialize_state(db)["assignments"],
        }
    finally:
        db.close()


@app.post("/api/arrangements/reset", status_code=200)
def reset_arrangements():
    """清空全部安排（含锁定）。"""
    db = next(get_db())
    try:
        deleted = db.query(models.Assignment).delete()
        db.commit()
        return {"deleted": deleted}
    finally:
        db.close()


@app.post("/api/arrangements/clear-locks", status_code=200)
def clear_locks():
    db = next(get_db())
    try:
        n = 0
        for a in db.scalars(select(models.Assignment).where(models.Assignment.locked.is_(True))).all():
            a.locked = False
            n += 1
        db.commit()
        return {"unlocked": n}
    finally:
        db.close()


@app.put("/api/arrangements/{participant_id}", response_model=schemas.AssignmentOut)
def upsert_assignment(participant_id: int, payload: schemas.AssignmentUpdate):
    """手动安排（移动）某位参与者；默认锁定。"""
    db = next(get_db())
    try:
        p = db.get(models.Participant, participant_id)
        if not p:
            raise HTTPException(404, "参与者不存在")
        t = db.get(models.Table, payload.table_id)
        if not t:
            raise HTTPException(404, "桌位不存在")
        if payload.seat_no > t.capacity:
            raise HTTPException(400, f"席位号超出该桌容量（{t.capacity}）")

        occupied = db.scalar(
            select(models.Assignment).where(
                models.Assignment.table_id == payload.table_id,
                models.Assignment.seat_no == payload.seat_no,
                models.Assignment.participant_id != participant_id,
            )
        )
        if occupied:
            other = db.get(models.Participant, occupied.participant_id)
            raise HTTPException(
                409,
                f"该席位已被 {other.name if other else occupied.participant_id} 占用"
                + ("（且已锁定）" if occupied.locked else ""),
            )

        a = db.scalar(
            select(models.Assignment).where(
                models.Assignment.participant_id == participant_id
            )
        )
        if a is None:
            a = models.Assignment(participant_id=participant_id)
            db.add(a)
        a.table_id = payload.table_id
        a.seat_no = payload.seat_no
        a.locked = payload.locked
        db.commit()
        db.refresh(a)
        return schemas.AssignmentOut(
            id=a.id,
            participant_id=p.id,
            participant_name=p.name,
            group_name=p.group_name,
            table_id=t.id,
            table_name=t.name,
            section_id=t.section_id,
            section_name=t.section.name if t.section else None,
            seat_no=a.seat_no,
            locked=a.locked,
        )
    finally:
        db.close()


@app.patch("/api/arrangements/{participant_id}/lock")
def set_lock(participant_id: int, locked: bool):
    db = next(get_db())
    try:
        a = db.scalar(
            select(models.Assignment).where(
                models.Assignment.participant_id == participant_id
            )
        )
        if not a:
            raise HTTPException(404, "该参与者当前没有安排")
        a.locked = locked
        db.commit()
        return {"participant_id": participant_id, "locked": locked}
    finally:
        db.close()


@app.delete("/api/arrangements/{participant_id}", status_code=204)
def delete_assignment(participant_id: int):
    """取消某位参与者的当前安排（含锁定）。"""
    db = next(get_db())
    try:
        a = db.scalar(
            select(models.Assignment).where(
                models.Assignment.participant_id == participant_id
            )
        )
        if not a:
            raise HTTPException(404, "该参与者当前没有安排")
        db.delete(a)
        db.commit()
        return Response(status_code=204)
    finally:
        db.close()


@app.get("/api/export")
def export_csv():
    """导出当前安排为 CSV（带 UTF-8 BOM，Excel 可直接打开）。"""
    db = next(get_db())
    try:
        rows = serialize_state(db)["assignments"]
        if not rows:
            raise HTTPException(400, "当前没有可导出的安排，请先求解")
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["参与者", "分组", "分区", "桌位", "席位号", "是否锁定", "备注"])
        note_map = {
            p.id: (p.note or "")
            for p in db.scalars(select(models.Participant)).all()
        }
        for r in rows:
            writer.writerow(
                [
                    r["participant_name"],
                    r["group_name"] or "",
                    r["section_name"] or "",
                    r["table_name"],
                    r["seat_no"],
                    "是" if r["locked"] else "否",
                    note_map.get(r["participant_id"], ""),
                ]
            )
        content = "\ufeff" + buf.getvalue()
        return Response(
            content=content,
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=seating.csv"},
        )
    finally:
        db.close()


# ----------------------------------------------------------------------
# 静态前端
# ----------------------------------------------------------------------
STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")
if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(os.path.join(STATIC_DIR, "index.html"))
