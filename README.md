# 活动席位编排（Seating Arrangement）

基于 **FastAPI + SQLite + 原生 JavaScript** 的活动席位编排 Web 应用。
支持录入参与者、桌位与分区容量约束，设置**必须同桌 / 禁止同桌 / 固定席位**规则，
用**可重复的约束搜索（CSP 回溯）**生成安排；可锁定部分席位后重新求解，
无解时给出**导致冲突的具体规则 / 锁定 / 容量约束**。

## 功能特性

- **参与者管理**：单个增删改，或通过 **CSV 批量导入**（支持 UTF-8/GBK、中英文表头）。
- **分区与桌位**：定义桌位座位数；可把桌位归入分区并设置分区容量上限。
- **三类编排规则**
  - 必须同桌（多条规则可传递：A~B、B~C ⇒ A、B、C 同桌）；
  - 禁止同桌（组内两两不同桌）；
  - 固定席位（固定到某桌，可精确到席位号；支持多人固定到同一桌）。
- **可重复求解**：内置基于回溯的约束满足求解器（MRV + 前向检查 + 位集传播）。
  相同数据 + 相同**随机种子** ⇒ 完全相同的结果；改变种子可获得不同的可行安排。
- **锁定后重排**：在席位图上手动入座 / 换人 / 解锁，锁定的席位在重新求解时保持不变。
- **无解诊断**：求解失败时用“删除法”计算一个极小冲突集合，明确指出
  是哪几条规则、哪些锁定席位或容量约束共同导致无解。
- **结果导出**：一键导出当前安排为 CSV（带 UTF-8 BOM，Excel 可直接打开）。
- **容器化**：提供 `Dockerfile` 与 `docker-compose.yml`，一键启动、数据持久化。

## 技术栈

| 层 | 技术 |
| --- | --- |
| 后端 | FastAPI、SQLAlchemy 2.x、SQLite、Pydantic v2 |
| 求解器 | 纯 Python 实现的 CSP 回溯搜索（无外部求解器依赖） |
| 前端 | 原生 HTML / CSS / JavaScript（无框架、无构建步骤） |
| 部署 | Uvicorn、Docker、Docker Compose |

## 目录结构

```
.
├── app/
│   ├── main.py        # FastAPI 入口与全部 REST API
│   ├── solver.py      # CSP 约束求解器（回溯 / 传播 / 无解诊断）
│   ├── models.py      # SQLAlchemy 模型
│   ├── schemas.py     # Pydantic 请求 / 响应模型
│   └── database.py    # 数据库连接与初始化
├── static/            # 原生前端（index.html / styles.css / app.js）
├── scripts/           # 求解器与 API 的自动化测试
├── examples/          # CSV 导入示例
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

## 一键启动（推荐，使用 Docker Compose）

前置要求：安装 Docker 与 Docker Compose（`docker compose` 子命令）。

```bash
docker compose up --build
```

启动后在浏览器访问：

- **应用首页：http://localhost:8000**
- 交互式 API 文档（Swagger UI）：http://localhost:8000/docs

SQLite 数据库保存在命名卷 `seating-data`（容器内 `/app/data/seating.db`）中，
容器重建后数据不丢失。

停止服务：

```bash
docker compose down        # 保留数据
docker compose down -v     # 同时删除数据卷（清空全部数据）
```

## 本地直接运行（开发）

要求 Python 3.11+。

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

访问 http://localhost:8000 。首次启动会自动在 `./data/seating.db` 创建数据库表。

## 配置项（环境变量）

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `DATABASE_URL` | `sqlite:///./data/seating.db` | SQLAlchemy 连接串。Docker 中默认为 `sqlite:////app/data/seating.db` |

如需自定义端口，可修改 `docker-compose.yml` 中 `ports` 的宿主机端口，
例如 `"18000:8000"` 后通过 http://localhost:18000 访问。

## 使用流程

1. **参与者**页：点击「导入 CSV」选择 `examples/participants.csv`，或手工新增。
2. **分区与桌位**页：创建分区（可设容量上限）和桌位（设座位数、选择所属分区）。
3. **规则**页：新增「必须同桌 / 禁止同桌 / 固定席位」规则，可随时停用 / 启用。
4. **编排结果**页：
   - 设置一个**种子**整数（如 `0`、`123`），点击「求解 / 重新求解」；
   - 点击任意席位可手动安排参与者（自动锁定）、换人、解锁或置空；
   - 调整锁定后再次「重新求解」，锁定席位保持不变；
   - 无解除非约束相互矛盾，页面会列出具体冲突来源；
   - 点击「导出 CSV」下载结果。

## CSV 导入格式

- 表头支持英文 `name,group,note` 或中文 `姓名,分组,备注`（列顺序不限）；
- 无表头时按 `姓名,分组,备注` 的顺序解析；
- 自动识别 UTF-8（含 BOM）/ GBK 编码；空行自动跳过。

示例见 [`examples/participants.csv`](examples/participants.csv)。

## 求解器与无解诊断说明

- 搜索采用 MRV（最小剩余值域）、前向检查、桌位粒度位集传播，
  并对“必须同桌”的传递闭包做并查集合并与容量预检。
- **可重复性**：种子为 `0` 时使用确定性的取值顺序；非零时以
  `(seed, 参与者)` 为种子对取值做确定性洗牌，因此结果可复现。
- 无解时，求解器把每条规则、每个锁定、每条分区容量、总容量都视为“标签”，
  通过逐个删除标签定位一个**极小冲突核心**（删除其中任一标签即可变为有解），
  前端据此展示具体是哪些约束在冲突。
- 搜索有节点上限保护（默认 200000，可在 API 请求中调整 `node_limit`），
  达到上限会明确返回“搜索超限”，而不是无限等待。

## REST API 摘要

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/state` | 获取全部数据（参与者/分区/桌位/规则/安排） |
| POST/PUT/DELETE | `/api/participants[/{id}]` | 参与者增改删 |
| POST | `/api/participants/import` | CSV 导入 |
| POST/PUT/DELETE | `/api/sections[/{id}]` | 分区增改删 |
| POST/PUT/DELETE | `/api/tables[/{id}]` | 桌位增改删 |
| POST/PUT/PATCH/DELETE | `/api/rules[/{id}]` | 规则增改 / 启停 / 删除 |
| POST | `/api/solve` | 求解（body：`{"seed": 0, "node_limit": 200000}`） |
| PUT/PATCH/DELETE | `/api/arrangements/{participant_id}` | 手动入座 / 锁定 / 置空 |
| POST | `/api/arrangements/reset` | 清空全部安排 |
| POST | `/api/arrangements/clear-locks` | 解除全部锁定 |
| GET | `/api/export` | 导出结果 CSV |

完整字段见 http://localhost:8000/docs 。

## 运行测试

```bash
# 求解器行为测试（无需启动服务）
python scripts/test_solver.py

# API 端到端测试（需先在 127.0.0.1:8123 启动服务）
DATABASE_URL="sqlite:////tmp/seating-test.db" \
  uvicorn app.main:app --host 127.0.0.1 --port 8123 &
python scripts/test_api.py
```
