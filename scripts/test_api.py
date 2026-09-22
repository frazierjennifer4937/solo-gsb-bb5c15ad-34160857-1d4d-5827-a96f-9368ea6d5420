"""端到端 API 测试：配合运行中的 uvicorn 使用。用法：python scripts/test_api.py"""
import csv
import io
import json
import urllib.request

BASE = "http://127.0.0.1:8123"


def req(method, path, body=None, raw=None, expect=None):
    url = BASE + path
    data = None
    headers = {}
    if raw is not None:
        data = raw
        headers["Content-Type"] = "multipart/form-data"
    elif body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r) as resp:
            text = resp.read().decode()
            code = resp.status
    except urllib.error.HTTPError as e:
        text = e.read().decode()
        code = e.code
    payload = None
    if text:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            payload = text
    if expect is not None:
        assert code == expect, f"{method} {path} expected {expect}, got {code}: {text[:200]}"
    return code, payload


passed = 0


def ok(cond, msg):
    global passed
    assert cond, msg
    passed += 1
    print("  ✓", msg)


# ---- 清空既有安排 / 数据：直接重置（参与者等保留接口无批量删除，用全新库） ----
req("POST", "/api/arrangements/reset", expect=200)

print("[A] CSV 导入参与者")
csv_text = "姓名,分组,备注\n张三,技术部,\n李四,技术部,\n王五,市场部,\n赵六,市场部,\n钱七,市场部,\n孙八,后勤部,\n周九,后勤部,\n吴十,后勤部,\n"

import uuid
import urllib.request as ureq

b = uuid.uuid4().hex
parts = [
    f"--{b}".encode(),
    b'Content-Disposition: form-data; name="file"; filename="p.csv"',
    b"Content-Type: text/csv",
    b"",
    csv_text.encode("utf-8"),
    f"--{b}--".encode(),
]
raw_body = b"\r\n".join(parts)
r2 = ureq.Request(
    BASE + "/api/participants/import", data=raw_body, method="POST",
    headers={"Content-Type": f"multipart/form-data; boundary={b}"},
)
with ureq.urlopen(r2) as resp:
    data = json.loads(resp.read().decode())
ok(data["added"] == 8, f"CSV 导入 8 人: {data}")

code, st = req("GET", "/api/state")
ok(len(st["participants"]) == 8, f"当前共 8 名参与者: {len(st['participants'])}")
names = {p["name"]: p["id"] for p in st["participants"]}
zhang, li, wang, zhao, qian, sun, zhou, wu = (
    names["张三"], names["李四"], names["王五"], names["赵六"],
    names["钱七"], names["孙八"], names["周九"], names["吴十"],
)

print("[B] 分区与桌位")
_, sec_a = req("POST", "/api/sections", {"name": "A区", "capacity": 4}, expect=201)
_, sec_b = req("POST", "/api/sections", {"name": "B区", "capacity": None}, expect=201)
_, t1 = req("POST", "/api/tables", {"name": "1号桌", "capacity": 4, "section_id": sec_a["id"]}, expect=201)
_, t2 = req("POST", "/api/tables", {"name": "2号桌", "capacity": 4, "section_id": sec_b["id"]}, expect=201)
_, t3 = req("POST", "/api/tables", {"name": "3号桌", "capacity": 4, "section_id": sec_b["id"]}, expect=201)
ok(True, "创建 2 个分区、3 张桌")

# 重名校验
code, _ = req("POST", "/api/tables", {"name": "1号桌", "capacity": 4})
ok(code == 409, "桌位重名返回 409")

print("[C] 规则 CRUD 与校验")
_, r_together = req("POST", "/api/rules", {
    "type": "together", "participant_ids": [zhang, li],
}, expect=201)
_, r_sep = req("POST", "/api/rules", {
    "type": "separate", "participant_ids": [wang, zhao, qian],
}, expect=201)
_, r_fixed = req("POST", "/api/rules", {
    "type": "fixed", "participant_ids": [sun], "table_id": t1["id"], "seat_no": 1,
}, expect=201)
ok(True, "创建 3 条规则")

# 校验：together 少于 2 人
code, err = req("POST", "/api/rules", {"type": "together", "participant_ids": [zhang]})
ok(code == 400, f"together 仅 1 人被拒绝: {err}")
# 校验：fixed 不带桌位
code, err = req("POST", "/api/rules", {"type": "fixed", "participant_ids": [zhang]})
ok(code == 400, "fixed 不带桌位被拒绝")
# 校验：fixed 席位号超容量
code, err = req("POST", "/api/rules", {
    "type": "fixed", "participant_ids": [zhang], "table_id": t1["id"], "seat_no": 99,
})
ok(code == 400, "fixed 席位号超容量被拒绝")
# 停用
_, upd = req("PATCH", f"/api/rules/{r_sep['id']}", {"enabled": False}, expect=200)
ok(upd["enabled"] is False, "规则可停用")

print("[D] 首次求解")
_, sol = req("POST", "/api/solve", {"seed": 123}, expect=200)
ok(sol["solvable"], "求解成功")
ok(len(sol["assignments"]) == 8, f"安排 8 人: {len(sol['assignments'])}")
asg = {a["participant_id"]: a for a in sol["assignments"]}
ok(asg[sun]["table_id"] == t1["id"] and asg[sun]["seat_no"] == 1, "孙八固定在 1号桌 1席")
ok(asg[zhang]["table_id"] == asg[li]["table_id"], "张三李四同桌")

# 可重复性
_, sol2 = req("POST", "/api/solve", {"seed": 123}, expect=200)
a1 = {(a["participant_id"], a["table_id"], a["seat_no"]) for a in sol["assignments"]}
a2 = {(a["participant_id"], a["table_id"], a["seat_no"]) for a in sol2["assignments"]}
ok(a1 == a2, "相同种子结果完全一致")

print("[E] 锁定席位后重新求解")
# 找到 1号桌当前的空席（孙八固定在 1 席），把王五锁过去
_, st_e = req("GET", "/api/state")
t1_used = {a["seat_no"] for a in st_e["assignments"] if a["table_id"] == t1["id"]}
free_e = next(no for no in range(1, t1["capacity"] + 1) if no not in t1_used)
code, _ = req("PUT", f"/api/arrangements/{wang}",
              {"table_id": t1["id"], "seat_no": free_e, "locked": True}, expect=200)
_, sol3 = req("POST", "/api/solve", {"seed": 999}, expect=200)
ok(sol3["solvable"], "锁定后重新求解成功")
wa = next(a for a in sol3["assignments"] if a["participant_id"] == wang)
ok(wa["table_id"] == t1["id"] and wa["seat_no"] == free_e and wa["locked"],
   f"王五仍在锁定的 1号桌 {free_e} 席")

print("[F] 制造无解：禁止同桌规则 vs 同桌锁定")
req("PATCH", f"/api/rules/{r_sep['id']}", {"enabled": False}, expect=200)  # 停用三人规则避免干扰
# 清空全部安排，从干净状态开始（fixed 规则仍会在求解时生效）
req("POST", "/api/arrangements/reset", expect=200)
# 新建：赵六与钱七禁止同桌；孙八 fixed 在 t1-1 不受影响
_, r_sep2 = req("POST", "/api/rules", {
    "type": "separate", "participant_ids": [zhao, qian],
}, expect=201)
# 把二人锁到 1号桌相邻席位（1 席是孙八的 fixed 位置，避开）
req("PUT", f"/api/arrangements/{zhao}",
    {"table_id": t1["id"], "seat_no": 2, "locked": True}, expect=200)
req("PUT", f"/api/arrangements/{qian}",
    {"table_id": t1["id"], "seat_no": 3, "locked": True}, expect=200)
_, unsat = req("POST", "/api/solve", {"seed": 1}, expect=200)
ok(not unsat["solvable"], "返回无解")
msgs = " | ".join(c["message"] for c in unsat["conflicts"])
kinds = {(c["kind"], c["id"]) for c in unsat["conflicts"]}
ok(("rule", r_sep2["id"]) in kinds, f"冲突包含禁止同桌规则: {msgs}")
ok(("lock", zhao) in kinds and ("lock", qian) in kinds,
   f"冲突包含赵六、钱七的两个锁定席位: {msgs}")
ok(all(c[0] != "section" for c in kinds), f"分区容量未被卷入: {msgs}")
print("   冲突说明：")
for c in unsat["conflicts"]:
    print("    -", c["message"])

print("[G] 解除锁定 + 停用规则后恢复可解")
req("POST", "/api/arrangements/clear-locks", expect=200)
req("PATCH", f"/api/rules/{r_sep2['id']}", {"enabled": False}, expect=200)
_, sol4 = req("POST", "/api/solve", {"seed": 7}, expect=200)
ok(sol4["solvable"], "冲突解除后重新有解")

print("[H] 总容量不足诊断")
# 当前 3 张桌共 12 座；删掉 3号桌后剩 8 座，再增第 9 人
req("DELETE", f"/api/tables/{t3['id']}", expect=204)
req("POST", "/api/participants", {"name": "郑十一", "group_name": "来宾"}, expect=201)
_, st2 = req("GET", "/api/state")
ok(len(st2["participants"]) == 9, "新增到 9 人")
_, unsat2 = req("POST", "/api/solve", {"seed": 1}, expect=200)
ok(not unsat2["solvable"], "9 人 8 座无解")
ok(any(c["kind"] == "capacity" for c in unsat2["conflicts"]),
   f"冲突指出总席位不足: {[c['message'] for c in unsat2['conflicts']]}")

print("[I] 导出 CSV")
# 恢复可解场景：删除第 9 人
pid9 = [p["id"] for p in st2["participants"] if p["name"] == "郑十一"][0]
req("DELETE", f"/api/participants/{pid9}", expect=204)
req("POST", "/api/solve", body={"seed": 7}, expect=200)
r3 = ureq.Request(BASE + "/api/export")
with ureq.urlopen(r3) as resp:
    exported = resp.read().decode()
    cd = resp.headers["Content-Disposition"]
ok("attachment" in cd, "导出带附件头")
rows = list(csv.reader(io.StringIO(exported.lstrip("﻿"))))
ok(rows[0] == ["参与者", "分组", "分区", "桌位", "席位号", "是否锁定", "备注"],
   f"导出表头正确: {rows[0]}")
ok(len(rows) == 9, f"导出 8 行数据 + 表头: {len(rows)}")

print("[J] 静态页面")
with ureq.urlopen(BASE + "/") as resp:
    html = resp.read().decode()
ok("活动席位编排" in html, "首页 HTML 可访问")
with ureq.urlopen(BASE + "/static/app.js") as resp:
    js = resp.read().decode()
ok("solve_full" not in js and "/api/solve" in js, "前端 JS 可访问")

print(f"\n端到端测试全部通过 ✅（{passed} 项断言）")
