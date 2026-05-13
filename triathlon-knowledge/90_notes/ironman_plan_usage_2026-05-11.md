# Coach Plan Usage Note: Ironman 226

source_path: `triathlon-knowledge/00_inbox/<private_ironman_226_plan>.numbers`
extract_path: `triathlon-knowledge/metadata/ironman_plan_extract_latest.json`
status: `analyzed_not_ingested`
trust_tier: `coach_historical_plan`
use_boundary: `logic_case_not_gold_standard`

## What It Is

这是一份用户历史铁三教练输出的 226 备战课表，不应当作为通用训练学事实直接进入 `01_approved`。它更适合作为：

- 个人历史教练方案样本
- 三项周内排布逻辑来源
- 当前自动课表的设计逻辑一致性参考
- 未来人工复核时的对照样例

## Useful Logic

1. 周结构很稳定：周一通常是骑行质量或骑行有氧，周二游泳，周三跑步例跑，周五休息或可选轻量训练，周六长骑/骑跑，周日长跑、游泳或恢复。
2. 长距离 226 备战中，周六长骑或长骑跑是核心锚点，比单纯把长骑放周日更贴近这份教练方案。
3. Brick 在中后期明显增多，适合用作“是否足够比赛专项化”的验证规则。
4. 周五经常被保护为休息/可选轻量日，可以作为总协调器的恢复日偏好。
5. 游泳经常承担技术、配合和有氧容量任务，不一定都算作下肢恢复压力，但高强度游泳仍要计入总压力提示。
6. 最后阶段有明显 taper/适应安排：比赛周降低总量，保留短刺激、场地适应、晨跑和比赛日。
7. 表单分为完赛组和进阶组，后续课表生成也应支持 `finish` 与 `advanced` 两个 profile，而不是只有一套固定逻辑。

## Do Not Use Blindly

1. 表内存在明显需要人工复核的数字，例如 `750%`、`120分钟0%跑`，以及若干超过 110% 的强度标记。
2. 每周都有 `参考周三例跑`，但这个外部课表没有包含在文件里，跑步负荷不能完整还原。
3. 游泳大量以米数呈现，不能只用显式分钟求周总量。
4. 这份课表没有包含用户当下疲劳、伤病、睡眠、CTL/ATL/TSB、Garmin 最近负荷和可用时间，不能直接复制到现在训练。

## Recommended Use In This Project

### 1. Metadata Case, Not Approved RAG

保留为 `coach_historical_plan`，不要复制到 `01_approved`。如果后续要入库，建议单独建 `03_coach_cases` 或只放 metadata，RAG 回答时必须说明这是“历史教练方案样本”，不是通用训练原则。

### 2. Logic Extraction

抽取以下可复用规则：

- `preferred_long_bike_day = Saturday`
- `protected_recovery_day = Friday`
- `weekly_anchor_run_day = Wednesday`
- `long_or_easy_run_after_long_bike = Sunday`
- `brick_frequency_increases_near_race = true`
- `race_week_contains_short_activation_and_site_adaptation = true`

### 3. Schedule Logic Validation

只验证整体设计逻辑是否同类，不做逐日逐课匹配，也不要求我们的课表复制教练表。可加入检查：

- 当前三项总协调器是否有每周 bike/run/swim 三项覆盖。
- 是否保护了至少一个恢复/可选轻量日。
- 周末是否有长骑或长骑跑锚点。
- 中后期是否逐步增加 brick 或比赛专项组合。
- 比赛周是否降总量而不是继续堆长课。
- 高强度日是否超过共享预算，若超过必须提示人工复核。
- 用户具体时间安排优先于教练表模板；如果用户只能周日长骑，则不应因为教练表偏周六而判定失败，只能提示“与历史教练表风格不同”。
- 恢复日高度依赖用户工作、家庭和场地安排，只能作为偏好提示，不能作为硬性失败条件。

### 4. Profile Dimension

后续计划生成建议增加 `plan_profile`：

- `finish`: 目标是稳定完赛，强度更保守，brick 增长更慢，恢复日保护更强。
- `advanced`: 目标是更高完成质量，允许更多专项强度和更长 brick，但仍受疲劳、伤病、可用时间和总高强度预算约束。

这两个 profile 只影响“课表设计倾向”，不能绕过用户可用时间、每日 preflight、伤痛状态和 Garmin 负荷。

### 5. Compare With Current Orchestrator

当前 `triathlon_plan_orchestrator_latest.json` 使用的是现有 intake 中的 `long_ride_day`，目前输出偏向周日长骑。若以这份教练方案做 226 备战风格校准，应考虑把长骑锚点改为周六，并把周日作为长跑、恢复跑、长游或恢复日候选。

## Next Implementation Step

新增一个 `ironman_plan_logic_check.py` 比较合适。它不生成课表，只读取：

- `triathlon-knowledge/metadata/ironman_plan_extract_latest.json`
- `triathlon-knowledge/metadata/triathlon_plan_orchestrator_latest.json`

然后输出：

- `schedule_shape_consistency`
- `weekend_long_anchor_consistency`
- `brick_specificity_consistency`
- `recovery_day_consistency`
- `taper_shape_consistency`
- `profile_fit: finish|advanced|mixed|unknown`
- `high_intensity_budget_warning`
