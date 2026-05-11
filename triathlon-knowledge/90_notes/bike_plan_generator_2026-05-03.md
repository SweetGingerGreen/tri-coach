# Bike 周级课型生成器记录 - 2026-05-03

## 结论

本轮完成 `bike_plan_generator.py` v0.11、`bike_plan_review.py` v0.2、`bike_plan_candidate.py` v0.2、`bike_plan_daily_preflight.py` v0.1、`rag_answer.py` 的 v0.11 复核解释入口，并同步把 `bike_plan_intake.py` 升到 v0.3。

它读取 `bike_plan_intake.py` 产出的 `ready_for_plan_frame`，生成周级课型分配。当前仍不生成每日训练课、不输出瓦数、不输出分钟、不输出组数、重复次数或具体间歇。

v0.2 在 v0.1 的课型清单上增加了 `week_slots`，把一周拆成 `easy`、`long`、`technical`、`hard` 这类槽位，但不绑定具体星期几。

v0.3 增加跨项负荷预算：跑步、游泳和力量训练负荷较高时，会保守下调 bike 高强度预算。

v0.4 增加本地知识库来源引用：每个课型和周内槽位都会带 `source_refs`，用于追溯到 RAG chunk。

v0.5 增加星期排程层：把 `week_slots` 放到具体星期和日期，但仍不生成每日训练课细节。

v0.6 增加人工复核视图：把每周 hard、long、跨项负荷、约束日和来源引用压成一行，方便人工抽查。

v0.7 把 `source_refs` 接到 RAG 问答器：用户可以追问某一周某一天为什么这样安排，问答器会定位 `weekday_schedule` 并读取对应来源 chunk。

v0.8 增加跨项排程占位：根据 bike hard、bike long 和用户不可训练日，标出跑步 hard、游泳 hard、下肢力量应避开的恢复窗口。

v0.9 增加 Markdown/CSV 复核导出：把 `review_view` 落成可人工抽查和后续修改的表格文件。

`rag_answer.py` v0.8 在单槽位解释之外，增加“解释第 N 周所有安排”的批量解释入口。

`bike_plan_intake.py` v0.3 和 `bike_plan_generator.py` v0.10 增加固定跨项日期冲突检测：如果用户提供固定跑步 hard、游泳 hard 或下肢力量日，生成器会和 bike hard、bike long、不可训练日形成的避让窗口相交，输出具体冲突。

`bike_plan_generator.py` v0.11 在复核 CSV 中增加人工编辑列，`bike_plan_review.py` v0.1 可以把人工复核 CSV 转成独立 override 文件。

`rag_answer.py` v0.9 增加两个确定性问答入口：解释固定跨项日期冲突，汇总人工复核 override。

`bike_plan_candidate.py` v0.1 增加第二版候选周级排程：读取第一版生成报告和人工 override，尝试按人工意见移动周级 slot，并单独输出候选报告和候选复核文件。

`bike_plan_review.py` v0.2 和 `bike_plan_candidate.py` v0.2 增加结构化 override 字段：`move_slot`、`blocked_day`、`protect_day`。候选生成器会优先读取这些机器字段，再退回自然语言解析。

`rag_answer.py` v0.10 增加候选差异解释入口：可以回答“第二版候选排程和第一版差在哪？”。

`bike_plan_daily_preflight.py` v0.1 增加每日训练课前置准入检查：只判断是否具备进入每日课表草案的数据，不生成每日训练课。

`bike_plan_daily_preflight.py` 继续增加 CSV 填写流程：可以导出 bike slot 表和固定跨项训练表，人工填表后再转回 preflight JSON。

`garmin_prefill_preflight.py` 增加 Garmin Connect 只读预填：优先读取本机 env 中的 Garmin 邮箱和密码在线登录，仍保留 `~/.garminconnect` tokenstore 回退，读取 Garmin 日历、训练准备度和睡眠，回填 preflight CSV 中 Garmin 能证明的字段。

`rag_answer.py` v0.11 增加 daily preflight 汇总入口：可以回答“进入每日训练课前还缺哪些数据？”。

`bike_plan_daily_draft.py` v0.1 增加每日训练课草案层：只在 preflight 已通过时生成 daily bike workout draft；模拟输入必须显式 `--allow-simulated`，避免把流程验证当真实处方。

`rag_answer.py` 继续增加 daily draft 汇总入口：可以回答“第1周每日训练课草案生成了什么？”。

`bike_plan_long_ride_nutrition_review.py` v0.1 增加长骑补给复核层：读取 daily draft 中的 `Long Ride`，连接 `骑行热量与碳水消耗计算模板`，估算热量和碳水消耗，但不生成真实补给处方。

`rag_answer.py` 继续增加 long ride nutrition review 汇总入口：可以回答“长骑补给复核结果是什么？”。

## 新增脚本

- `bike_plan_generator.py`
- `eval_bike_plan_generator.py`
- `bike_plan_candidate.py`
- `eval_bike_plan_candidate.py`
- `bike_plan_daily_preflight.py`
- `eval_bike_plan_daily_preflight.py`
- `bike_plan_daily_draft.py`
- `eval_bike_plan_daily_draft.py`
- `bike_plan_long_ride_nutrition_review.py`
- `eval_bike_plan_long_ride_nutrition_review.py`
- `garmin_prefill_preflight.py`

## 输入边界

生成器只接受 intake 状态为：

- `ready_for_plan_frame`

以下情况会直接 `blocked`：

- intake 仍有 `missing_data`
- intake 有 `warnings`
- 缺少 `plan_frame`

## 输出内容

输出状态：

- `generated_weekly_type_allocation`

每周输出：

- `week`
- `week_start`
- `phase_hint`
- `workload_state`
- `volume_1_10`
- `intensity_1_10`
- `bike_days_budget`
- `bike_hours_budget`
- `high_intensity_budget`
- `assigned_high_intensity_count`
- `workout_type_allocation`
- `week_slots`
- `weekday_schedule`
- `cross_sport_placeholders`
- `review_view`
- `source_grounding`
- `guardrails`

## 课型规则 v0.1

基础课型：

- `Endurance Ride`: 周内基础耐力骑。
- `Long Ride`: 每周或隔周长骑，优先放在最宽裕的一天。
- `Cadence Workout`: 技术和经济性练习，不作为主要高强度。

高强度课型：

- `Power Intervals`: 基础期只作为少量可选神经/力量感刺激。
- `Threshold Ride`: build 阶段可作为主要高强度课型。
- `Brick`: specific build 阶段可作为专项课型；如果骑跑都含高强度，计入周总强度。

减载和 peak：

- `deload` 和 `peaking` 周默认移除高强度课型，只保留耐力和轻量技术练习。

## 周内槽位规则 v0.2

槽位类型：

- `easy`: 普通耐力衔接槽。
- `long`: 长骑槽，放在本周最宽裕的位置。
- `technical`: 踏频、技术、经济性练习槽。
- `hard`: 阈值、power interval 或高强度 brick 槽。

槽位边界：

- `week_slots` 不是每日课表，不代表具体星期几。
- 每个 slot 只包含 `slot_type`、`workout_type`、`intensity_class`、`priority` 和放置原则。
- 不输出 watts、minutes、intervals、sets、reps 等细节字段。
- hard 槽数量必须等于 `assigned_high_intensity_count`，且不能超过 `high_intensity_budget`。

## 跨项负荷预算 v0.3

`bike_plan_intake.py` 现在先计算只看骑行的基础预算：

- 当前疲劳高或有伤病：bike 高强度预算为 0。
- 每周骑行不超过 3 天：bike 高强度预算为 1。
- 每周骑行 4 天及以上：bike 高强度预算为 2。

然后检查跑步、游泳和力量训练：

- `run_hours >= 6`: 认为跑步负荷偏高。
- `swim_hours >= 4`: 认为游泳负荷偏高。
- `strength_sessions >= 2`: 认为力量训练负荷偏高。
- `run_hours + swim_hours >= 9`: 认为跨项耐力负荷偏高。

如果出现以上任一标记，bike 高强度预算下调 1，但不会低于 0。

intake 的 `intensity_budget` 会保留：

- `base_bike_high_intensity_budget`
- `cross_sport_adjustment`
- `other_sports_load`
- `max_bike_high_intensity_sessions_per_week`

生成器只读取最终的 `max_bike_high_intensity_sessions_per_week`，所以它天然会遵守跨项负荷下调后的预算。

## 来源引用规则 v0.4

生成器现在会从本地向量库 SQLite 文件读取 chunk metadata：

- `triathlon-knowledge/metadata/vectors/triathlon_core_v2_bge_m3.sqlite`

当前版本使用本地词法召回，不调用 embedding 或 chat model，所以本地模型不在线也不影响生成器运行。

每个课型会追加：

- `source_refs`
- `source_ref_boundary`

每个周内槽位会继承对应课型的 `source_refs`。

`source_refs` 只保留追溯 metadata：

- `chunk_id`
- `title`
- `domain`
- `trust_level`
- `page`
- `heading`
- `source_path`
- `matched_terms`
- `score`

它不会输出 chunk 正文、excerpt、snippet、分钟、瓦数、组数或具体间歇，避免周级生成器越界成每日训练课。

当前主要课型来源包括：

- `Endurance Ride`
- `Long Ride`
- `Cadence Workout`
- `Power Intervals`
- `Threshold Ride`
- `Brick`

## 星期排程规则 v0.5

生成器现在会输出 `weekday_schedule`，它只把 slot 绑定到星期和日期：

- `day`
- `day_label`
- `date`
- `slot`
- `slot_type`
- `workout_type`
- `intensity_class`
- `source_refs`
- `schedule_rule`
- `detail_boundary`

排程规则：

- `long` 优先放在 intake 的 `availability.long_ride_day`。
- 如果约束里出现“周一不能训练”这类信息，会避开对应星期。
- `hard` 避开 `long` 的相邻日，保留恢复间隔。
- `technical` 放在剩余低冲突日。
- `easy` 填补剩余可用日。

排程边界：

- `weekday_schedule` 不是每日课表。
- 它不会输出 watts、minutes、intervals、sets、reps。
- 它只解决“这类训练槽位放在星期几”，不解决“具体怎么骑”。

## 人工复核视图 v0.6

生成器现在会输出顶层 `review_view`：

- `status`
- `schema_version`
- `review_boundary`
- `preferred_long_day`
- `blocked_days`
- `cross_sport_load`
- `cross_sport_adjustment`
- `rows`

每个 `rows` 项对应一周，压缩展示：

- `week`
- `week_start`
- `phase_hint`
- `workload_state`
- `bike_days_budget`
- `bike_hours_budget`
- `scheduled_days`
- `hard_summary`
- `long_summary`
- `high_intensity_budget`
- `assigned_high_intensity_count`
- `cross_sport_flags`
- `blocked_days`
- `preferred_long_day`
- `attention_flags`
- `review_status`
- `source_refs`
- `review_boundary`

当前会标记的 `attention_flags`：

- `schedule_slot_count_mismatch`: 排程数量和 slot 数量不一致。
- `duplicate_scheduled_day`: 同一星期被重复使用。
- `scheduled_on_blocked_day`: 排到了用户约束不可训练日。
- `long_not_on_preferred_day`: 长骑没有放在用户偏好的 long day。
- `hard_adjacent_to_long`: hard 和 long 相邻。
- `exceeds_high_intensity_budget`: 分配高强度超过预算。
- `hard_slot_in_deload`: 减载周出现 hard。
- `cross_sport_load_with_hard_slot`: 跑/游/力量负荷高时仍保留 hard，需要人工看恢复。
- `missing_source_refs`: 缺来源引用。

复核边界：

- `review_view` 只是人工检查摘要，不是训练处方。
- `source_refs` 只保留 metadata，不输出 chunk 正文。
- 不输出 watts、minutes、intervals、sets、reps。

## 槽位解释问答 v0.7

`rag_answer.py` 现在会优先识别这类问题：

- “为什么第1周周二安排 easy / Endurance Ride？”
- “解释一下 week 1 Tuesday 的 Endurance Ride 来源。”
- “这个周日 long ride 为什么这样排？”

处理链路：

1. 读取 `triathlon-knowledge/metadata/bike_plan_generator_latest.json`。
2. 根据问题里的 week、day、slot type 或 workout type 定位 `weekday_schedule`。
3. 读取该 slot 的 `source_refs`。
4. 用 `source_refs.chunk_id` 到本地 SQLite 向量库取回来源 chunk。
5. 返回解释答案和 `[S1]` 来源。

这个入口是确定性的：

- 不依赖 chat model 是否在线。
- 不先做普通向量检索，避免 slot 追问跑偏。
- 仍通过 `rag_answer.answer_question()` 返回统一结构。

解释内容只回答：

- 这周是什么阶段。
- 这个 slot 放在哪一天。
- 为什么按当前排程规则放这里。
- hard/long/约束日/attention flag 是否需要人工复核。
- 依据哪些 source chunk。

边界：

- 不生成每日训练课。
- 不输出 watts、minutes、intervals、sets、reps。
- 如果要继续细化为训练课，仍要补当天可用时长、跑步/游泳排程、疲劳和疼痛状态。

## 整周解释问答 v0.8

`rag_answer.py` 现在还会识别这类问题：

- “解释第1周所有安排的依据。”
- “为什么第 3 周整周这样排？”
- “week 2 全部安排的来源是什么？”

处理链路：

1. 读取 `triathlon-knowledge/metadata/bike_plan_generator_latest.json`。
2. 定位用户指定的 week。
3. 汇总该周 `weekday_schedule` 中所有 slot。
4. 汇总该周 `review_view` 的 hard、long、约束日、复核标记和跨项占位。
5. 用该周所有 slot 的 `source_refs.chunk_id` 读取来源 chunk。
6. 返回整周解释答案和 `[S1]`、`[S2]`、`[S3]` 来源。

回答内容包括：

- 这一周每个 bike slot 放在哪一天。
- 这一周的阶段、负荷状态、骑行天数预算和高强度预算。
- hard、long、不可训练日和跨项避让窗口。
- 本周是否有 `attention_flags` 需要人工复核。

边界：

- 不生成每日训练课。
- 不输出 watts、minutes、intervals、sets、reps。
- 只解释当前周级排程，不直接修改课表。

## 跨项排程占位 v0.8

生成器现在会在每周输出 `cross_sport_placeholders`：

- `status`
- `boundary`
- `other_sports_input`
- `active_caution_flags`
- `bike_hard_days`
- `bike_long_days`
- `run_hard_avoid_days`
- `swim_hard_caution_days`
- `strength_lower_body_avoid_days`
- `rules`

它的作用不是生成跑步、游泳或力量训练课，而是给后续排课层提供冲突窗口：

- 跑步 hard 不要放在 bike hard 同日或相邻日。
- 跑步 hard 不要放在 bike long 当日或之后恢复日。
- 下肢力量不要放在 bike hard 恢复窗口或 bike long 当日。
- 游泳 hard 不要放在 bike hard 同日；如果游泳负荷高，也避开次日。
- 用户约束日会同步进入跑步、游泳和力量的避让窗口。

示例：如果本周 bike hard 在周二、bike long 在周日、周一不可训练：

- `run_hard_avoid_days`: 周一、周二、周三、周日
- `swim_hard_caution_days`: 周一、周二
- `strength_lower_body_avoid_days`: 周一、周二、周三、周日

跨项占位边界：

- 不生成跑步课表。
- 不生成游泳课表。
- 不生成力量训练课。
- 不输出 watts、minutes、intervals、sets、reps。

## 复核导出 v0.9

生成器现在可以直接输出人工复核文件：

```bash
python3 bike_plan_generator.py --input triathlon-knowledge/metadata/bike_plan_intake_latest.json --write-report --write-review-files
```

默认生成：

- `triathlon-knowledge/metadata/bike_plan_review_latest.md`
- `triathlon-knowledge/metadata/bike_plan_review_latest.csv`

Markdown 文件适合直接阅读和人工批注；CSV 文件适合放进表格软件里筛选、改状态或后续接入排课系统。

每行对应一周，包含：

- `week`
- `week_start`
- `phase_hint`
- `workload_state`
- `bike_days_budget`
- `bike_hours_budget`
- `scheduled_days`
- `hard_summary`
- `long_summary`
- `run_hard_avoid_days`
- `swim_hard_caution_days`
- `strength_lower_body_avoid_days`
- `attention_flags`
- `review_status`
- `source_chunk_ids`
- `review_boundary`

导出边界：

- 只导出周级复核摘要。
- 只导出来源 `chunk_id`，不导出 chunk 正文。
- 不生成每日训练课。
- 不输出 watts、minutes、intervals、sets、reps。

## 固定跨项日期冲突检测 v0.10

intake 现在支持在 `other_sports` 中提供固定跨项日期：

- `fixed_run_hard_days`
- `fixed_swim_hard_days`
- `fixed_strength_lower_body_days`

这些字段可以使用英文星期或中文星期，例如：

```json
{
  "other_sports": {
    "fixed_run_hard_days": ["Tuesday", "周日"],
    "fixed_swim_hard_days": ["周二"],
    "fixed_strength_lower_body_days": ["Sunday"]
  }
}
```

intake 会把它们标准化成：

- `day`
- `day_label`
- `raw`

generator 会在每周 `cross_sport_placeholders` 中输出：

- `fixed_cross_sport_days`
- `conflict_status`
- `cross_sport_conflicts`

当前检测规则：

- 固定跑步 hard 如果落在 `run_hard_avoid_days`，标记 `fixed_run_hard_on_avoid_day`。
- 固定游泳 hard 如果落在 `swim_hard_caution_days`，标记 `fixed_swim_hard_on_caution_day`。
- 固定下肢力量如果落在 `strength_lower_body_avoid_days`，标记 `fixed_strength_lower_body_on_avoid_day`。

如果任一周出现冲突，人工复核行会追加：

- `fixed_cross_sport_day_conflict`

复核导出的 Markdown/CSV 现在也有 `cross_sport_conflicts` 列。

边界：

- 只检测冲突，不移动课表。
- 不生成跑步、游泳或力量训练课。
- 不输出 watts、minutes、intervals、sets、reps。

## 人工复核 override v0.11

复核 CSV 现在额外包含人工文字列，供人工修改：

- `human_review_status`
- `review_comment`
- `override_request`

也包含三个机器可读列，供下一版候选排程直接执行：

- `move_slot`
- `blocked_day`
- `protect_day`

推荐人工复核流程：

1. 打开 `triathlon-knowledge/metadata/bike_plan_review_latest.csv`。
2. 如果某一周通过，可以不填这三列。
3. 如果某一周需要改，在 `human_review_status` 中写 `override_requested`、`needs_attention`、`修改`、`需修改` 等。
4. 在 `review_comment` 写复核原因。
5. 在 `override_request` 写希望下一版排程怎么调整。
6. 如果调整意图很明确，优先填写结构化字段，例如 `move_slot=hard`、`blocked_day=Tuesday`、`protect_day=Sunday`。
7. 运行：

```bash
python3 bike_plan_review.py --review-csv triathlon-knowledge/metadata/bike_plan_review_latest.csv --plan-report triathlon-knowledge/metadata/bike_plan_generator_latest.json --write-override
```

默认生成：

- `triathlon-knowledge/metadata/bike_plan_review_override_latest.json`

如果 CSV 没有人工作出的覆盖请求，状态是：

- `no_overrides`

如果 CSV 有人工覆盖请求，状态是：

- `overrides_ready`

override 文件包含：

- `review_summary`
- `review_items`
- `overrides`
- `structured_override`
- `guardrails`

边界：

- override 文件只表达人工复核意见和覆盖请求。
- 不直接修改 `bike_plan_generator_latest.json`。
- 不生成每日训练课。
- 不输出 watts、minutes、intervals、sets、reps。

## 复核解释问答 v0.12

`rag_answer.py` 现在会识别两类复核问题。

第一类是固定跨项日期冲突解释：

- “第 1 周为什么标记了固定跨项日期冲突？”
- “解释 week 1 的 conflict。”
- “为什么这一周有 fixed_cross_sport_day_conflict？”

处理链路：

1. 读取 `triathlon-knowledge/metadata/bike_plan_generator_latest.json`，或 CLI 指定的 `--bike-plan-report`。
2. 定位指定 week。
3. 汇总该周的 `attention_flags`、`cross_sport_conflicts` 和跨项避让窗口。
4. 读取该周 bike slot 的 `source_refs`，给出来源标记。
5. 返回冲突原因和人工复核建议。

第二类是人工 override 汇总：

- “哪些周被人工要求修改？”
- “override 文件里有没有需要调整的周？”
- “复核里哪几周需要改？”

处理链路：

1. 读取 `triathlon-knowledge/metadata/bike_plan_review_override_latest.json`，或 CLI 指定的 `--review-override`。
2. 查看 `review_summary` 和 `overrides`。
3. 如果状态是 `no_overrides`，明确说明当前没有人工修改请求。
4. 如果状态是 `overrides_ready`，列出 week、review status、comment 和 override request。

边界：

- 只解释冲突和人工复核意见。
- 不直接修改原始生成报告。
- 不生成每日训练课。
- 不输出 watts、minutes、intervals、sets、reps。

## 第二版候选周级排程 v0.13

`bike_plan_candidate.py` 现在可以读取：

- `triathlon-knowledge/metadata/bike_plan_generator_latest.json`
- `triathlon-knowledge/metadata/bike_plan_review_override_latest.json`

然后生成独立的第二版候选文件：

```bash
python3 bike_plan_candidate.py --plan-report triathlon-knowledge/metadata/bike_plan_generator_latest.json --override triathlon-knowledge/metadata/bike_plan_review_override_latest.json --write-candidate --write-review-files
```

默认生成：

- `triathlon-knowledge/metadata/bike_plan_candidate_latest.json`
- `triathlon-knowledge/metadata/bike_plan_candidate_review_latest.md`
- `triathlon-knowledge/metadata/bike_plan_candidate_review_latest.csv`

当前 v0.13 只支持很窄的人工 override 文本解析：

- “第1周避开周二 bike hard”
- “保留周日 long”

如果存在可用替代日，候选层会把对应周的 `hard` slot 从指定避让日移走，并重新计算跨项占位、冲突和复核视图。

输出会在 `candidate_metadata` 中保留：

- `status`
- `source_plan_report`
- `source_override_report`
- `applied_overrides`
- `unresolved_overrides`
- `guardrails`

边界：

- 不覆盖 `bike_plan_generator_latest.json`。
- 不修改人工 override 文件。
- 只调整周级 slot 的星期位置。
- 不生成每日训练课。
- 不输出 watts、minutes、intervals、sets、reps。
- 无法解析或无法安全执行的人工意见会进入 `candidate_metadata.unresolved_overrides`。

## 结构化 override 与候选差异问答 v0.14

复核 CSV 现在支持三个机器字段：

- `move_slot`: 要移动的周级 slot，例如 `hard`。
- `blocked_day`: 这个 slot 需要避开的星期，例如 `Tuesday`、`周二`。
- `protect_day`: 需要保护不动的长骑日，例如 `Sunday`、`周日`。

示例：

```csv
move_slot,blocked_day,protect_day
hard,Tuesday,Sunday
```

`bike_plan_review.py` 会把这些字段写入 override 文件中的 `structured_override`：

- `move_slot`
- `blocked_days`
- `protect_day`
- `protect_days`

`bike_plan_candidate.py` 会优先读取 `structured_override`。如果没有结构化字段，才退回 v0.13 的自然语言解析。

`rag_answer.py` 新增候选差异解释：

```bash
python3 rag_answer.py "第二版候选排程和第一版差在哪？" --provider dry-run
```

它会读取：

- `triathlon-knowledge/metadata/bike_plan_generator_latest.json`
- `triathlon-knowledge/metadata/bike_plan_candidate_latest.json`

回答内容包括：

- 哪些周发生变化。
- 哪个 slot 从哪一天移动到哪一天。
- 哪些 override 已应用。
- 哪些 override 仍未解决。
- 为什么不能直接覆盖第一版。

当前正式 latest 由于没有人工 override，候选差异结果是：

- `candidate_metadata.status`: `no_candidate_changes`

边界：

- 结构化 override 仍然只作用于周级 slot。
- 候选差异问答只解释第一版和候选版的周级星期变化。
- 不生成每日训练课。
- 不输出 watts、minutes、intervals、sets、reps。

## 每日训练课 preflight v0.15

`bike_plan_daily_preflight.py` 是进入每日训练课草案前的准入层。它读取当前候选周级计划：

- `triathlon-knowledge/metadata/bike_plan_candidate_latest.json`

并生成一个人工填写模板：

```bash
python3 bike_plan_daily_preflight.py --plan-report triathlon-knowledge/metadata/bike_plan_candidate_latest.json --write-template --write-csv-template --write-report
```

默认生成：

- `triathlon-knowledge/metadata/bike_plan_daily_preflight_input_latest.json`
- `triathlon-knowledge/metadata/bike_plan_daily_preflight_slots_latest.csv`
- `triathlon-knowledge/metadata/bike_plan_daily_preflight_fixed_sessions_latest.csv`
- `triathlon-knowledge/metadata/bike_plan_daily_preflight_latest.json`

preflight 输入模板要求人工补齐：

- `daily_availability`: 每个 bike slot 当天可骑行分钟数、当天是否可骑。
- `daily_status`: 每个 bike slot 当天疲劳、疼痛、睡眠质量。
- `fixed_sessions`: 固定跑步、游泳、力量训练，以及这些训练是否可移动。

人工优先填 CSV：

1. `bike_plan_daily_preflight_slots_latest.csv`: 每个 bike slot 一行，填 `available_minutes`、`can_bike`、`fatigue`、`pain_status`、`sleep_quality`。
2. `bike_plan_daily_preflight_fixed_sessions_latest.csv`: 每个固定跑步、游泳、力量训练一行，填 `sport`、`session_type`、`movable`。

填完 CSV 后转回 JSON 并重新跑 preflight：

```bash
python3 bike_plan_daily_preflight.py --plan-report triathlon-knowledge/metadata/bike_plan_candidate_latest.json --input-from-csv --write-report
```

也可以先用 Garmin Connect 只读预填：

```bash
python3 garmin_prefill_preflight.py --auth-mode env --env-file /path/to/local/.env --as-of 2026-05-03 --write
python3 bike_plan_daily_preflight.py --plan-report triathlon-knowledge/metadata/bike_plan_candidate_latest.json --input-from-csv --write-report
```

本次 Garmin 预填结果：

- 从本机 env 找到 `GARMIN_EMAIL` 和 `GARMIN_PASSWORD`，用 `auth_mode=env_credentials` 在线登录成功。
- 报告只记录 `garmin_email_present=true` 和 `garmin_password_present=true`，不记录邮箱和密码值。
- readiness 报告只保留训练计划需要的摘要字段，不保留 Garmin 用户 ID 或设备 ID。
- 读取 `2026-05-03` training readiness：score 67，映射为 `fatigue=normal`。
- 读取 `2026-05-03` sleep：score 89，映射为 `sleep_quality=good`。
- 回填 24 个 bike slot 的 `fatigue` 和 `sleep_quality`，共 48 个单元格。
- env 模式重跑时，因为 CSV 已有 Garmin 回填值，新增写入为 0。
- `2026-05-05` 到 `2026-06-28` 范围内 Garmin 日历没有可导入的固定训练。

Garmin 不能证明这些字段，所以仍需人工确认：

- `available_minutes`
- `can_bike`
- `pain_status`
- `fixed_sessions.movable`

输出状态：

- `ready_for_daily_draft`: 可以进入每日训练课草案层。
- `needs_more_daily_data`: 还缺 daily 输入数据。
- `blocked_by_daily_risk`: 有疼痛、高疲劳或不可移动跨项冲突，不能进入每日训练课。

当前正式 latest 的状态是：

- `needs_more_daily_data`
- 8 周仍缺 daily 输入
- 0 周 ready
- 0 周 blocked

`rag_answer.py` 现在可以回答：

```bash
python3 rag_answer.py "进入每日训练课前还缺哪些 daily preflight 数据？" --provider dry-run
```

回答会读取：

- `triathlon-knowledge/metadata/bike_plan_daily_preflight_latest.json`

边界：

- preflight 只做准入检查。
- 不生成每日训练课。
- 不输出 watts、minutes、intervals、sets、reps。
- 疼痛、高疲劳或不可移动跨项冲突会阻止进入每日训练课草案层。

## 每日训练课草案 v0.1

`bike_plan_daily_draft.py` 是 preflight 之后的第一版 daily workout draft 层。

正式 `latest` 仍然读取真实 preflight：

```bash
python3 bike_plan_daily_draft.py --write-output --output triathlon-knowledge/metadata/bike_plan_daily_draft_latest.json
```

当前正式状态仍是：

- `blocked_by_preflight`
- 原因：真实 `bike_plan_daily_preflight_latest.json` 仍是 `needs_more_daily_data`

模拟验证使用单独文件，不覆盖正式 latest：

```bash
python3 bike_plan_daily_draft.py \
  --preflight-report triathlon-knowledge/metadata/bike_plan_daily_preflight_simulated_validation.json \
  --output triathlon-knowledge/metadata/bike_plan_daily_draft_simulated_validation.json \
  --review-md triathlon-knowledge/metadata/bike_plan_daily_draft_review_simulated.md \
  --review-csv triathlon-knowledge/metadata/bike_plan_daily_draft_review_simulated.csv \
  --allow-simulated \
  --write-output \
  --write-review-files
```

本次模拟草案结果：

- `daily_draft_generated`
- `simulation_mode=true`
- 8 周、24 条 bike workout
- 每周 3 条：周二 `Endurance Ride`，周三 `Cadence Workout`，周日 `Long Ride`
- simulated 假设：easy/technical 60 分钟，long 120 分钟，`can_bike=yes`，`pain_status=none`
- 草案会输出结构化 warmup/main/cooldown 或 cadence drills，但仍标记为 `daily_workout_draft_requires_human_review_before_real_training`

`rag_answer.py` 可以解释 daily draft：

```bash
python3 rag_answer.py "第1周每日训练课草案生成了什么？" --provider dry-run --daily-draft-report triathlon-knowledge/metadata/bike_plan_daily_draft_simulated_validation.json
```

## 长骑补给复核 v0.1

`bike_plan_long_ride_nutrition_review.py` 读取 daily draft 中的长骑，并连接 approved 层的 `骑行热量与碳水消耗计算模板`：

```bash
python3 bike_plan_long_ride_nutrition_review.py \
  --daily-draft-report triathlon-knowledge/metadata/bike_plan_daily_draft_simulated_validation.json \
  --output triathlon-knowledge/metadata/bike_plan_long_ride_nutrition_review_simulated_validation.json \
  --review-md triathlon-knowledge/metadata/bike_plan_long_ride_nutrition_review_simulated.md \
  --review-csv triathlon-knowledge/metadata/bike_plan_long_ride_nutrition_review_simulated.csv \
  --allow-simulated \
  --write-output \
  --write-review-files
```

当前模拟复核结果：

- `long_ride_nutrition_review_generated`
- `simulation_mode=true`
- 8 次长骑，全部已复核
- 使用估算 IF `0.65`，FTP `193 W`，估算平均功率 `125.5 W`
- 每次 120 分钟长骑估算约 `1028.0 kcal`，碳水消耗约 `125.3 g`
- 处方状态全部是 `blocked_missing_individual_tolerance_data`
- 不生成每小时摄入克数、比赛日时间表或真实补给处方

`rag_answer.py` 可以解释长骑补给复核：

```bash
python3 rag_answer.py "长骑补给复核结果是什么？" --provider dry-run --long-ride-nutrition-review-report triathlon-knowledge/metadata/bike_plan_long_ride_nutrition_review_simulated_validation.json
```

## 样例输出

当前样例输入：

- 目标赛事：2026-09-20 示例大铁
- 当前 FTP：193 W
- CTL/ATL/TSB：65 / 72 / -7
- 每周骑行：3 天，6 小时

生成前 8 周：

- week 1: base / load / Endurance Ride, Long Ride, Cadence Workout
- week 2: base / load / Endurance Ride, Long Ride, Cadence Workout
- week 3: base / load_plus / Endurance Ride, Long Ride, Cadence Workout
- week 4: base / deload / Endurance Ride, Long Ride, Cadence Workout
- week 5: base / load / Endurance Ride, Long Ride, Cadence Workout
- week 6: base / load / Endurance Ride, Long Ride, Cadence Workout
- week 7: base / load_plus / Endurance Ride, Long Ride, Cadence Workout
- week 8: base / deload / Endurance Ride, Long Ride, Cadence Workout

对应槽位：

- `long`
- `technical`
- `easy`

对应星期排程：

- 周二：easy
- 周三：technical
- 周日：long

对应人工复核：

- `review_status`: `ok`
- `attention_flags`: 空
- `blocked_days`: 周一
- `source_refs`: 指向 Endurance Ride、Cadence Workout、Long Ride 对应 chunk

## 报告文件

- `triathlon-knowledge/metadata/bike_plan_generator_latest.json`
- `triathlon-knowledge/metadata/bike_plan_generator_eval_latest.json`
- `triathlon-knowledge/metadata/bike_plan_review_latest.md`
- `triathlon-knowledge/metadata/bike_plan_review_latest.csv`
- `triathlon-knowledge/metadata/bike_plan_review_eval_latest.md`
- `triathlon-knowledge/metadata/bike_plan_review_eval_latest.csv`
- `triathlon-knowledge/metadata/bike_plan_review_override_latest.json`
- `triathlon-knowledge/metadata/bike_plan_review_override_eval_latest.json`
- `triathlon-knowledge/metadata/bike_plan_review_eval_latest.json`
- `triathlon-knowledge/metadata/bike_plan_conflict_rag_eval_latest.json`
- `triathlon-knowledge/metadata/bike_plan_review_override_rag_eval_latest.json`
- `triathlon-knowledge/metadata/bike_plan_candidate_latest.json`
- `triathlon-knowledge/metadata/bike_plan_candidate_review_latest.md`
- `triathlon-knowledge/metadata/bike_plan_candidate_review_latest.csv`
- `triathlon-knowledge/metadata/bike_plan_candidate_eval_latest.json`
- `triathlon-knowledge/metadata/bike_plan_candidate_eval_latest_candidate.json`
- `triathlon-knowledge/metadata/bike_plan_candidate_rag_eval_latest.json`
- `triathlon-knowledge/metadata/bike_rag_eval_candidate_diff_latest.json`
- `triathlon-knowledge/metadata/bike_plan_daily_preflight_input_latest.json`
- `triathlon-knowledge/metadata/bike_plan_daily_preflight_slots_latest.csv`
- `triathlon-knowledge/metadata/bike_plan_daily_preflight_fixed_sessions_latest.csv`
- `triathlon-knowledge/metadata/bike_plan_daily_preflight_latest.json`
- `triathlon-knowledge/metadata/garmin_preflight_prefill_latest.json`
- `triathlon-knowledge/metadata/bike_plan_daily_draft_latest.json`
- `triathlon-knowledge/metadata/bike_plan_daily_draft_simulated_validation.json`
- `triathlon-knowledge/metadata/bike_plan_daily_draft_review_simulated.md`
- `triathlon-knowledge/metadata/bike_plan_daily_draft_review_simulated.csv`
- `triathlon-knowledge/metadata/bike_plan_long_ride_nutrition_review_latest.json`
- `triathlon-knowledge/metadata/bike_plan_long_ride_nutrition_review_simulated_validation.json`
- `triathlon-knowledge/metadata/bike_plan_long_ride_nutrition_review_simulated.md`
- `triathlon-knowledge/metadata/bike_plan_long_ride_nutrition_review_simulated.csv`
- `triathlon-knowledge/metadata/bike_plan_daily_preflight_eval_latest.json`
- `triathlon-knowledge/metadata/bike_plan_daily_preflight_eval_result.json`
- `triathlon-knowledge/metadata/bike_plan_daily_draft_eval_latest.json`
- `triathlon-knowledge/metadata/bike_plan_daily_draft_eval_result.json`
- `triathlon-knowledge/metadata/bike_plan_long_ride_nutrition_review_eval_latest.json`
- `triathlon-knowledge/metadata/bike_plan_long_ride_nutrition_review_eval_result.json`
- `triathlon-knowledge/metadata/bike_rag_eval_daily_preflight_latest.json`
- `triathlon-knowledge/metadata/bike_rag_eval_daily_draft_latest.json`
- `triathlon-knowledge/metadata/bike_rag_eval_long_ride_nutrition_review_latest.json`

## 验收结果

- `python3 -m py_compile bike_plan_intake.py eval_bike_plan_intake.py bike_plan_generator.py eval_bike_plan_generator.py bike_plan_review.py eval_bike_plan_review.py bike_plan_candidate.py eval_bike_plan_candidate.py bike_plan_daily_preflight.py eval_bike_plan_daily_preflight.py bike_plan_daily_draft.py eval_bike_plan_daily_draft.py bike_plan_long_ride_nutrition_review.py eval_bike_plan_long_ride_nutrition_review.py garmin_prefill_preflight.py rag_answer.py eval_bike_rag.py eval_rag.py`：通过。
- `python3 eval_bike_plan_intake.py`：5/5 passed。
- `python3 eval_bike_plan_generator.py`：5/5 passed。
- `python3 eval_bike_plan_review.py`：3/3 passed。
- `python3 eval_bike_plan_candidate.py`：1/1 passed。
- `python3 eval_bike_plan_daily_preflight.py`：6/6 passed。
- `python3 eval_bike_plan_daily_draft.py`：5/5 passed。
- `python3 eval_bike_plan_long_ride_nutrition_review.py`：4/4 passed。
- `python3 eval_bike_rag.py --mode answer --report triathlon-knowledge/metadata/bike_rag_eval_answer_latest.json`：12/12 passed。
- `python3 eval_rag.py --mode answer`：3/3 passed。
- `python3 bike_plan_generator.py --input triathlon-knowledge/metadata/bike_plan_intake_latest.json --write-report --write-review-files`：生成周级课型分配报告和人工复核文件。
- `python3 bike_plan_review.py --review-csv triathlon-knowledge/metadata/bike_plan_review_latest.csv --plan-report triathlon-knowledge/metadata/bike_plan_generator_latest.json --write-override`：生成人工复核 override 文件。
- `python3 bike_plan_candidate.py --plan-report triathlon-knowledge/metadata/bike_plan_generator_latest.json --override triathlon-knowledge/metadata/bike_plan_review_override_latest.json --write-candidate --write-review-files`：生成第二版候选周级排程和候选复核文件。
- `python3 bike_plan_daily_preflight.py --plan-report triathlon-knowledge/metadata/bike_plan_candidate_latest.json --write-template --write-csv-template --write-report`：生成每日训练课前置 JSON/CSV 输入模板和 preflight 报告。
- `python3 garmin_prefill_preflight.py --auth-mode env --env-file /path/to/local/.env --as-of 2026-05-03 --write`：使用本机 Garmin env 凭据在线登录，只读预填 `fatigue` 和 `sleep_quality`。
- `python3 bike_plan_daily_preflight.py --plan-report triathlon-knowledge/metadata/bike_plan_candidate_latest.json --input-from-csv --write-report`：将 Garmin 预填后的 CSV 转回 JSON 并重跑 preflight。
- `python3 bike_plan_daily_draft.py --write-output --output triathlon-knowledge/metadata/bike_plan_daily_draft_latest.json`：正式 latest 因真实 preflight 未通过而输出 `blocked_by_preflight`。
- `python3 bike_plan_daily_draft.py --preflight-report triathlon-knowledge/metadata/bike_plan_daily_preflight_simulated_validation.json --output triathlon-knowledge/metadata/bike_plan_daily_draft_simulated_validation.json --review-md triathlon-knowledge/metadata/bike_plan_daily_draft_review_simulated.md --review-csv triathlon-knowledge/metadata/bike_plan_daily_draft_review_simulated.csv --allow-simulated --write-output --write-review-files`：用 simulated preflight 跑通 daily draft 流程。
- `python3 rag_answer.py "第1周每日训练课草案生成了什么？" --provider dry-run --daily-draft-report triathlon-knowledge/metadata/bike_plan_daily_draft_simulated_validation.json`：能解释 simulated daily draft，并标明不能直接当真实训练处方。
- `python3 bike_plan_long_ride_nutrition_review.py --write-output --output triathlon-knowledge/metadata/bike_plan_long_ride_nutrition_review_latest.json`：正式 latest 因正式 daily draft 未生成而输出 `blocked_by_daily_draft`。
- `python3 bike_plan_long_ride_nutrition_review.py --daily-draft-report triathlon-knowledge/metadata/bike_plan_daily_draft_simulated_validation.json --output triathlon-knowledge/metadata/bike_plan_long_ride_nutrition_review_simulated_validation.json --review-md triathlon-knowledge/metadata/bike_plan_long_ride_nutrition_review_simulated.md --review-csv triathlon-knowledge/metadata/bike_plan_long_ride_nutrition_review_simulated.csv --allow-simulated --write-output --write-review-files`：用 simulated daily draft 跑通长骑热量/碳水复核。
- `python3 rag_answer.py "长骑补给复核结果是什么？" --provider dry-run --long-ride-nutrition-review-report triathlon-knowledge/metadata/bike_plan_long_ride_nutrition_review_simulated_validation.json`：能解释长骑补给复核，并标明不生成真实补给处方。

## 下一步

正式训练建议仍建议做人工 daily 输入补齐：

1. 打开 `triathlon-knowledge/metadata/bike_plan_daily_preflight_slots_latest.csv`。
2. `fatigue` 和 `sleep_quality` 已由 Garmin 初步预填；继续填每个 bike slot 当天可骑分钟数、是否可骑、疼痛。
3. 打开 `triathlon-knowledge/metadata/bike_plan_daily_preflight_fixed_sessions_latest.csv`。
4. 填固定跑步、游泳、力量训练，以及是否可移动。
5. 运行 `python3 bike_plan_daily_preflight.py --plan-report triathlon-knowledge/metadata/bike_plan_candidate_latest.json --input-from-csv --write-report`。
6. 只有状态变成 `ready_for_daily_draft` 后，才进入每日训练课草案。

如果继续跳过人工输入，只能沿 simulated 文件推进工程验证。下一层可做：

1. daily draft 人工复核 override。
2. 生成“正式课表发布前检查器”，确认不再引用 simulated 输入。
3. 把真实 Garmin/历史长骑平均功率、体重、排汗率、碳水耐受接入补给复核输入。
