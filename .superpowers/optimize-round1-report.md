# 优化轮 1 报告：锚点两级回退 few-shot + 评估 Decimal 粗检

- 分支/提交：`optimize-round1` @ `b0d5dea`（feat: 锚点两级回退 few-shot + 评估 Decimal 粗检）
- 测试：`uv run pytest` **106 passed**（103 基线 + 1 条 Decimal 粗检单测 + 2 条新增 few-shot 参数化回归）
- 改动文件：`data/fewshot.yaml`、`src/aigis/evaluator.py`、`tests/test_evaluator.py`

## 一、锚点回退 few-shot（错题 id 12 根因处置）

### 根因复核（真库核实，与原假设不一致）

原假设：锚点"北京南站"是面状地物、不在 `osm_pois`，点查子查询空导致 count 0。
真库核实结果（docker exec aigis-postgis psql）：

| 表 | 记录数 | 明细 |
|---|---|---|
| `osm_pois` | 3 | osm_id 155610958「北京南站」POINT(116.371 39.865) 等及京津城际/普速/京沪高速场 |
| `osm_areas` | 4 | 北京南站本体 + 三个场（面状） |

**结论：北京南站在两张表都有**（`osm_pois` 有点、`osm_areas` 有面），锚点子查询并非空。

count 0 的真实原因是**数据本身**：以 osm_pois 锚点计，最近便利店 1134 米 > 1000 米半径，1 公里内确实无便利店（2 公里内有 7 家，全市共 604 家）。即 id 12 错题更可能是"答案标注/生成 SQL 的锚点或半径与数据分布不匹配"，而非点查 miss。

### 交付内容

仍按计划追加第 16 条 few-shot（`data/fewshot.yaml`），SQL 原文采用两级回退模式：

```sql
SELECT count(*) AS cnt FROM osm_pois p WHERE p.shop='convenience' AND ST_DWithin(p.geom::geography, COALESCE((SELECT geom FROM osm_pois WHERE name LIKE '%北京南站%' ORDER BY osm_id LIMIT 1), (SELECT ST_Centroid(geom) FROM osm_areas WHERE name LIKE '%北京南站%' ORDER BY osm_id LIMIT 1)), 1000)
```

- 真库验证：SQL 可执行、COALESCE 语法正确，返回 cnt=0（真实数据结果，非执行错误）。
- 该样例的价值在于示范"锚点先查点表、miss 则回退面表 ST_Centroid"的稳健模式（如"颐和园"等仅存在于 osm_areas 的面状锚点场景，现有样例第 18 行已是单级查 areas，两级回退覆盖更全）。
- 回归：`tests/test_prompt.py` 两套参数化测试（validator 合规 + 真库执行）经 `load_fewshot()` 动态加载自动覆盖新样例，各 +1 条（本条返回 0 行但 `r.ok=True`，通过）。

## 二、in_range Decimal 守卫（评估题 36/40 根因）

- 根因：psycopg 将 PG `numeric` 列映射为 `decimal.Decimal`，原守卫 `isinstance(n, (int, float))` 拒绝 `Decimal('7.75')` → in_range 误记 None（无法粗检），区间异常被漏报。
- 修复（`src/aigis/evaluator.py`）：`from decimal import Decimal`；守卫改为 `isinstance(n, (int, float, Decimal))`。
- 测试（`tests/test_evaluator.py` 新增 `test_run_eval_in_range_decimal_cell`）：单行单列 `Decimal("7.75")`、区间 [5,10] → in_range True（修复前为 None）。

## 验证步骤

```powershell
cd E:/aiGIS
git log --oneline -1                  # b0d5dea
uv run pytest                         # 106 passed
docker exec aigis-postgis psql -U aigis_readonly -d aigis -c "SELECT 'pois' AS tbl, count(*) FROM osm_pois WHERE name LIKE '%北京南站%' UNION ALL SELECT 'areas', count(*) FROM osm_areas WHERE name LIKE '%北京南站%';"
```
