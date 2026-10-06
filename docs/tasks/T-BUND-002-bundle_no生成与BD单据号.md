# T-BUND-002：`bundle_no` 生成器 + `BD` 打菲单号（复用 `take_doc_no`）

| 项 | 内容 |
| --- | --- |
| 模块 | bundling |
| 负责人 | backend-dev |
| 状态 | `todo`（Q-B18 未闭环前不得把格式正则落成 DB CHECK） |
| 优先级 | P1 |
| 依赖 | T-BUND-001 |
| 被依赖 | T-BUND-005b（审核逐手生成码）、T-BUND-006（标签） |
| 关联设计 | [`modules/03-打菲.md`](../modules/03-打菲.md) §5.2、B1/B2/B13；[`P1-打菲-实施说明.md`](../modules/P1-打菲-实施说明.md) §5 |
| 关联 ADR | [ADR-0016](../adr/0016-打菲按手与扫码得件数.md)（格式修订）、[ADR-0004](../adr/0004-打菲二维码编码方案.md)（码内容纯文本） |
| 估算 | 0.5d |

## 目标

提供两个纯口径并接线：
1. `BD-{YYYYMMDD}-{6 位}` 单据号 —— **直接复用** `core/numbering.py::take_doc_no(prefix="BD")`，
   **不新增计数器表、不新增迁移**；
2. `bundle_no` 构造/校验纯函数，供 `T-BUND-005b` 审核时逐手调用。

## 范围

**要做**：
- [ ] `backend/app/modules/bundling/service/numbering.py`（或 `bundle_no.py`）：
  - `build_bundle_no(doc_no: str, size_code: str, hands_seq: int, item_seq: int = 1) -> str`
    —— 手序号补零 2 位、件序号补零 4 位（一码一手恒 `0001`）
  - `parse_bundle_no(...)` / `is_valid_bundle_no(...)`：按 Q-B18 结论实现，**尺码码长度可变**
  - `next_doc_no(session, doc_date=None) -> str`：包一层 `take_doc_no(prefix="BD")`
- [ ] `backend/tests/modules/test_bundling_numbering.py`：
  - 格式用例（`L01` / `XL02` / `XXL01` / `3XL01`）
  - `take_doc_no` 并发不重复（20 并发同 `(doc_date, "BD")`）
  - 跨天重置、回滚语义（照 `core/numbering.py` docstring 的口径）

**不做**：
- **不新增 `bundling_doc_no_sequences`**（`04 §7.17` 的 `prefix` 列即为复用而设）
- 不生成 `>0001` 的件序号（Q-B10 未闭环）
- 不把 `bundle_no` 正则写成 DB `CHECK`（Q-B18 未闭环；见 `03 B2` 的 CHECK 现与 TC-24 矛盾）
- 不改 `core/numbering.py`（只调用）

## 将要改动的文件

| 文件 | 类型 | 说明 |
| --- | --- | --- |
| `backend/app/modules/bundling/service/__init__.py` | 新增 | 包入口，重导出本卡纯函数（供后续 service 复用） |
| `backend/app/modules/bundling/service/numbering.py` | 新增 | 单据号接线 + `bundle_no` 构造/解析 |
| `backend/tests/modules/test_bundling_numbering.py` | 新增 | 纯函数 + 并发取号 |

## 实现要点（必读规范）

- [ ] 遵守 `docs/09 §2.1`（`BD` 前缀）与 `docs/09 §2.4`（`bundle_no` 格式）
- [ ] **复用 `core/numbering.py::take_doc_no`**（`app/core/numbering.py:129`）：它已
      并发安全（`INSERT … ON CONFLICT DO NOTHING` + `UPDATE … RETURNING`），
      且按 `(doc_date, prefix)` 分组、按天重置；业务日期用 `business_today()`
- [ ] 测试必须显式传 `doc_date`，避免跨零点失败（`docs/10 §10`）
- [ ] `bundle_no` 只由服务端生成，**不接受前端传入**（`03 §7` 并发方案②）
- [ ] Q-B18 闭环前：`is_valid_bundle_no` 允许尺码码 1~3 位，但**在 DB 层不落 CHECK**；
      代码里留 `TODO(业务待确认: Q-B18)`

## 验收标准

- [ ] `uv run pytest tests/modules/test_bundling_numbering.py -q` 全部通过
- [ ] `bundle_no` 样例通过：`BD-20261018-000031-L01-0001`、`…-XL02-0001`、
      `…-XXL01-0001`、`…-3XL01-0001`
- [ ] 20 并发取 `BD` 单号**无重复**、序号连续（`core/numbering.py` 既有守卫可复用）
- [ ] `from app.modules.bundling.service import build_bundle_no, next_doc_no` 可用
- [ ] 无新增迁移；`alembic check` 零漂移
- [ ] 单文件 ≤400 行；闸门 1-3 本地预跑通过

## 测试清单

| # | 用例 | 期望 | 结果 |
| --- | --- | --- | --- |
| TC-BN-01 | `build_bundle_no("BD-20261018-000031","XL",2)` | `BD-20261018-000031-XL02-0001` | |
| TC-BN-02 | 尺码码 3 位 `XXL` / `3XL` | 不抛错、原样嵌入 | |
| TC-BN-03 | `parse_bundle_no` 往返一致 | 通过 | |
| TC-BN-04 | `next_doc_no` 20 并发 | 无重复、无空洞 | |
| TC-BN-05 | `take_doc_no` 复用**不新建表** | 无迁移、`cutting_doc_no_sequences` 出现 `BD` 行 | |
| TC-BN-06 | 跨天：`doc_date` 变化 → 序号从 1 重来 | 通过 | |
| TC-BN-07 | 回滚后重取 | 号退回且从未可观测（仅断言机制） | |

## 实际改动（完成后回填）

| 文件 | 行数 | 说明 |
| --- | --- | --- |
| 待回填 | | |

**提交记录**：
- `<hash>` feat(bundling): `bundle_no` 生成器 + 复用 `take_doc_no` 取 `BD` 单号

## 遗留问题

| # | 问题 | 登记到 |
| --- | --- | --- |
| Q-B18 | 尺码码变长与正则冲突 | `P1-打菲-实施说明.md` §7 |

## 自检清单

对照 `AGENTS.md` §9 逐条勾选后才可置 `done`。

## 变更记录

| 日期 | 变更内容 | 操作人 |
| --- | --- | --- |
| 2026-10-06 | 初版：复用 `take_doc_no(prefix="BD")`，明确不新增计数器表；格式正则待 Q-B18 | AI |
