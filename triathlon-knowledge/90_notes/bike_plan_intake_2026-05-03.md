# Bike 课表生成输入层记录 - 2026-05-03

## 结论

本轮完成 bike 课表生成器的前置输入层 v0.1。

这一步不直接生成每日训练课。它负责判断用户资料是否足够进入课表生成，并把目标赛事、FTP、近期负荷、可训练时间、疲劳和伤病状态整理成结构化 `plan_frame`。

## 新增脚本

- `bike_plan_intake.py`
- `eval_bike_plan_intake.py`

## 输入 schema

核心输入分为 6 组：

- `goal`: 目标赛事名称、日期、距离、优先级。
- `current`: 当前日期、FTP、FTP 测试日期、CTL/ATL/TSB、疲劳、伤病状态。
- `recent_load`: 最近四到六周骑行小时、TSS、高强度次数；如果已有 CTL/ATL/TSB，可作为替代。
- `availability`: 每周可骑天数、总小时、长骑日、最长骑行时间、硬约束。
- `other_sports`: 跑步、游泳、力量训练负荷。
- `athlete_id`: 后续接用户档案时使用。

## 状态输出

- `needs_more_data`: 缺目标赛事、FTP、近期负荷、可训练时间或恢复状态。
- `blocked_by_risk`: 目标赛事日期已经早于当前日期等硬风险。
- `ready_for_plan_frame`: 可以进入下一步计划框架生成。

## 计划框架

当状态为 `ready_for_plan_frame` 时，输出：

- `macrocycle`: `season_to_target_race`
- `mesocycle`: `block`
- `microcycle`: `week`
- `week_fields`: `week_start`, `competition`, `primary_goal`, `secondary_goal`
- `load_fields`: `volume_1_10`, `intensity_1_10`, `workload_state`
- `phase_fields`: `hypertrophy`, `strength`, `strength_power`, `power`, `peaking`
- `intensity_budget`
- `week_skeleton`

`week_skeleton` 只标注周起始日期、距离比赛周数、阶段提示和负荷状态，不包含每日训练课，避免在缺少后续规则前提前编课。

## 高强度预算

当前 v0.1 采用保守规则：

- 每周骑行天数不超过 3 天时，默认最多 1 次 bike 高强度。
- 疲劳偏高或存在伤病/疼痛时，默认 bike 高强度预算为 0。
- brick 中如果骑和跑都含高强度，要计入一周总高强度，不要额外叠加。

## RAG 连接方式

输入层不直接调用生成模型。它输出后续需要检索的 `rag_queries`：

- 周期化模板：目标赛事、FTP、可训练时间、base/build/peak、workload。
- 基础期课型：Endurance Ride、Long Ride、Cadence Workout、Power Intervals、Threshold Ride。
- Brick 边界：bike-run、高强度计数和 Ironman 风险收益。

后续真正生成周课表时，应先用这些 query 召回依据，再由应用层规则生成训练结构。

## 验收结果

- `python3 -m py_compile bike_plan_intake.py eval_bike_plan_intake.py`：通过。
- `python3 eval_bike_plan_intake.py`：3/3 passed。
- `python3 bike_plan_intake.py --template`：可输出空白 JSON 模板。
- 完整样例输入已生成：`triathlon-knowledge/metadata/bike_plan_intake_latest.json`。

## 下一步

下一步可以做 bike plan generator v0：

1. 读取 `bike_plan_intake.py` 产出的 `plan_frame`。
2. 根据每周可训练天数和小时，生成“周级课型分配”，仍不急着细化到瓦数和分钟。
3. 先覆盖 3 天/周和 4 天/周两个常见版本。
4. 把每日课表生成继续放在更后面，等周级结构通过 eval 后再做。
