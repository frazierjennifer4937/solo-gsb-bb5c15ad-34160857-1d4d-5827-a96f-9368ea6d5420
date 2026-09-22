"""端到端 API 冒烟测试（基于 TestClient）：python -m tests.test_api"""
import io
import os
import sys
import tempfile

# 每个进程使用独立临时数据库
_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ["SEAT_DB"] = _tmp.name

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

client = TestClient(app)

failed = 0


def check(name, cond, extra=""):
    global failed
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  {extra}" if extra else ""))
    if not cond:
        failed += 1


# ---- 演示数据 ----
r = client.post("/api/demo")
check("载入演示数据", r.status_code == 200)
state = client.get("/api/state").json()
check("演示数据含 14 名参与者", len(state["participants"]) == 14)
check("演示数据含 4 张桌", len(state["tables"]) == 4)
check("演示数据含 4 条规则", len(state["rules"]) == 4)

# ---- 求解 ----
r = client.post("/api/solve", json={"seed": 42})
check("演示场景求解成功", r.json()["feasible"], str(r.json().get("conflicts")))
s1 = client.get("/api/state").json()
check("14 人全部落座", len(s1["assignments"]) == 14)

# 可重复性
r = client.post("/api/solve", json={"seed": 42})
s2 = client.get("/api/state").json()
m1 = {a["participant_id"]: a["table_id"] for a in s1["assignments"]}
m2 = {a["participant_id"]: a["table_id"] for a in s2["assignments"]}
check("相同种子结果一致", m1 == m2)

# 规则生效：主持人（pid=7）固定到 1 号桌
tid_t1 = next(t["id"] for t in s2["tables"] if t["name"] == "1号桌")
check("固定席位生效", m2[7] == tid_t1, str(m2[7]))

# 必须同桌：前三名
tables_of_first3 = {m2[p] for p in (1, 2, 3)}
check("必须同桌生效", len(tables_of_first3) == 1)

# 禁止同桌：P1 与 P4
check("禁止同桌生效", m2[1] != m2[4])

# ---- 锁定后重新求解 ----
# 把 P10 锁到 3 号桌，重新求解后 P10 必须仍在 3 号桌
tid_t3 = next(t["id"] for t in s2["tables"] if t["name"] == "3号桌")
r = client.post("/api/assignments/lock",
                json={"participant_id": 10, "table_id": tid_t3, "locked": True})
check("锁定席位", r.status_code == 200)
r = client.post("/api/solve", json={"seed": 99})
check("带锁定重新求解成功", r.json()["feasible"], str(r.json().get("conflicts")))
s3 = client.get("/api/state").json()
m3 = {a["participant_id"]: a["table_id"] for a in s3["assignments"]}
check("锁定者保留席位", m3[10] == tid_t3)

# ---- 无解诊断 ----
# P1、P2、P3 已必须同桌；把 P1 固定到 3 号桌、P2 固定到 4 号桌 => 结构矛盾
tid_t4 = next(t["id"] for t in s3["tables"] if t["name"] == "4号桌")
r = client.post("/api/rules", json={
    "rule_type": "fixed", "pid": 1, "tid": tid_t3, "note": "故意制造冲突甲"})
rid_a = r.json()["id"]
check("新增固定规则甲", r.status_code == 201)
r = client.post("/api/rules", json={
    "rule_type": "fixed", "pid": 2, "tid": tid_t4, "note": "故意制造冲突乙"})
rid_b = r.json()["id"]
check("新增固定规则乙", r.status_code == 201)
r = client.post("/api/solve", json={"seed": 1})
js = r.json()
check("矛盾场景无解", not js["feasible"])
check("返回具体冲突原因", bool(js["conflicts"]) and "不同桌位" in js["conflicts"][0]["reason"],
      js["conflicts"][0]["reason"][:100] if js["conflicts"] else "")
check("冲突关联到两条固定规则",
      set(js["conflicts"][0]["rule_ids"]) == {rid_a, rid_b},
      str(js["conflicts"][0]["rule_ids"]))

# 删除其中一条冲突规则后恢复可解
client.delete(f"/api/rules/{rid_b}")
r = client.post("/api/solve", json={"seed": 1})
check("删除一条冲突规则后恢复可解", r.json()["feasible"], str(r.json().get("conflicts")))
client.delete(f"/api/rules/{rid_a}")

# ---- 容量无解 ----
client.post("/api/assignments/clear")
t1 = next(t for t in client.get("/api/state").json()["tables"] if t["name"] == "1号桌")
# 1 号桌容量 4，选 5 名不涉及任何规则的参与者（9-13）锁进去
for pid in range(9, 14):
    client.post("/api/assignments/lock",
                json={"participant_id": pid, "table_id": t1["id"], "locked": True})
r = client.post("/api/solve", json={"seed": 1})
js = r.json()
check("超员锁定导致无解", not js["feasible"])
check("说明超员桌位",
      any("1号桌" in c["reason"] for c in js["conflicts"]),
      js["conflicts"][0]["reason"])

# ---- 清空/解锁 ----
client.post("/api/assignments/clear")
check("清空席位", len(client.get("/api/state").json()["assignments"]) == 0)

# ---- CSV 导入 ----
csv_bytes = "姓名,备注\n新人甲,嘉宾\n新人乙,\n".encode("gbk")
r = client.post(
    "/api/participants/import-csv",
    files={"file": ("p.csv", io.BytesIO(csv_bytes), "text/csv")})
check("GBK CSV 导入", r.status_code == 200 and r.json()["imported"] == 2, r.text)

csv_bad = "foo,bar\nx,y\n".encode()
r = client.post(
    "/api/participants/import-csv",
    files={"file": ("p.csv", io.BytesIO(csv_bad), "text/csv")})
check("无法识别表头时每行都作为数据", r.status_code == 200 and r.json()["imported"] == 2, r.text)

# ---- 导出 ----
client.post("/api/solve", json={"seed": 5})
r = client.get("/api/export.csv")
check("导出 CSV", r.status_code == 200 and "姓名" in r.text and "桌位" in r.text)
check("导出包含新导入参与者", "新人甲" in r.text)

# ---- CRUD 校验 ----
r = client.post("/api/rules", json={"rule_type": "must_together", "pids": [1]})
check("单人必须同桌被拒", r.status_code == 422)
r = client.post("/api/rules", json={"rule_type": "fixed", "pid": 8, "tid": t1["id"]})
r = client.post("/api/rules", json={"rule_type": "fixed", "pid": 8, "tid": t1["id"]})
check("重复固定席位被拒", r.status_code == 422)
r = client.post("/api/tables", json={"name": "X", "capacity": 0})
check("零容量桌位被拒", r.status_code == 422)

# ---- 静态页面 ----
r = client.get("/")
check("首页可访问", r.status_code == 200 and "活动席位编排" in r.text)
r = client.get("/static/app.js")
check("静态 JS 可访问", r.status_code == 200)

os.unlink(_tmp.name)
print()
if failed:
    print(f"{failed} 个断言失败")
    sys.exit(1)
print("全部通过")
