# 铁三大脑 RAG 第二层 reference 入库记录 - 2026-05-03

## 结论

第二层 reference 已开始正式处理。本轮完成三件事：

1. 将第一批清单中建议进入 `02_reference` 的 15 份资料补齐 frontmatter，并复制到 `triathlon-knowledge/02_reference/<domain>/`。
2. 针对 `01_approved` 缺少 bike 独立资料的问题，从旧第二层 OCR 资料 `Triathlete Magazine's Complete Triathlon Book` 中抽出 `Bike Training` 专章，作为 bike approved 资料入库。
3. 将 inbox 新增的两份 Excel 转成 bike 领域的工具型知识卡，放入 `01_approved/bike`，不把整张表格逐格切入 RAG。

## 关于旧第二层 bike 资料

旧第二层里没有发现一本纯骑行专项书。可用的骑行内容主要来自：

- `Triathlete Magazines Complete Triathlon Book ... /ocr/...md`

这本书的 OCR 版本可读，并且目录里明确有 `Chapter Four: BIKE TRAINING`。本轮没有把整本书升入 `01_approved`，而是只把 `Bike Training` 专章抽成：

- `triathlon-knowledge/01_approved/bike/triathlete_magazine_complete_triathlon_book_bike_training.md`

这样做的原因是：整本书仍适合作为 `race/reference`，但骑行专章可以直接补足 `bike` 领域召回缺口。

## 两份新 Excel 的入库形态

### 热量计算.xlsx

入库为：

- `triathlon-knowledge/01_approved/bike/cycling_energy_carbohydrate_calculator.md`

类型：

- `knowledge_type: calculator_template`

使用方式：

- RAG 负责召回这张计算卡。
- 后续应用层应把公式实现成真正的计算函数。
- 不把 Excel 的空白格和布局逐格入库。

关键字段：

- 输入：FTP、运动时长、平均功率、总效率。
- 输出：IF、机械功、总能量、碳水比例、碳水克数、碳水速率、脂肪克数、脂肪速率。
- 当前示例：FTP 193 W、240 min、平均功率 125 W，对应总能量约 2048.6 kcal、碳水约 248.4 g。

边界：

- 更适合相对稳态骑行。
- 表格注释显示适用范围为 IF <= 1 且 IF > 0.55。
- QR 和底物比例是估算，不能替代实验室代谢测试。

### 周期训练计划模板.xlsx

入库为：

- `triathlon-knowledge/01_approved/bike/cycling_power_periodization_template.md`

类型：

- `knowledge_type: periodization_template`

使用方式：

- 作为后续骑行功率课表生成的结构模板。
- RAG 召回模板结构，应用层再填入目标赛事、FTP、近期负荷和可训练时间。

关键结构：

- `Macrocycle`
- `Mesocycle`
- `Microcycle`
- `Hypertrophy / Strength / Str-Power / Power / Peaking`
- `Volume 1-10`
- `Intensity 1-10`
- `WorkLoad`

## 本轮生成文件

新增 approved bike：

- `triathlon-knowledge/01_approved/bike/triathlete_magazine_complete_triathlon_book_bike_training.md`
- `triathlon-knowledge/01_approved/bike/cycling_energy_carbohydrate_calculator.md`
- `triathlon-knowledge/01_approved/bike/cycling_power_periodization_template.md`

新增 reference：

- `triathlon-knowledge/02_reference/nutrition/高级运动营养学（第2版） (丹·贝纳多特).md`
- `triathlon-knowledge/02_reference/nutrition/中国居民膳食指南（2022） (中国营养学会).md`
- `triathlon-knowledge/02_reference/run/悦动空间·跑步训练 汉森马拉松训练法 (（美）卢克·汉弗莱，凯斯·汉森，凯文·汉斯著；王晓刚译).md`
- `triathlon-knowledge/02_reference/run/刷新PB：跑步提速指南 (【美】霍尔·希格登  谢维译).md`
- `triathlon-knowledge/02_reference/run/无伤跑法 (戴剑松, 郑家轩).md`
- `triathlon-knowledge/02_reference/run/无伤跑法2：跑步技术优化与训练提升 (戴剑松).md`
- `triathlon-knowledge/02_reference/swim/The Swimming Drill Book, 2E (Guzman, Ruben).md`
- `triathlon-knowledge/02_reference/swim/力争上游 100个游泳技巧完全图解=THE 100 BEST SWIMMING DRILLS (（美）布莱斯卢塞罗（BLYTHE LUCERO）著).md`
- `triathlon-knowledge/02_reference/swim/游泳运动系统训练：运动原理、肌肉训练、运动损伤的预防 (伊恩·麦克劳德 (Ian Mcleod)).md`
- `triathlon-knowledge/02_reference/strength/Triphasic Training (Cal Dietz Ben Peterson).md`
- `triathlon-knowledge/02_reference/strength/Triphasic Training II (Cal Dietz Mike T Nelson) 三相训练 2.md`
- `triathlon-knowledge/02_reference/strength/核心评估与训练：核心能力的精准测试与针对性发展（修订版） (美国人体运动出版社).md`
- `triathlon-knowledge/02_reference/recovery/人体解剖学彩色图谱·运动系统 (沃纳·普拉策(Werner Platzer)).md`
- `triathlon-knowledge/02_reference/recovery/运动按摩(肌肉训练彩色解剖图谱) (阿比盖尔·埃尔斯沃思, 佩姬·奥尔特曼著).md`
- `triathlon-knowledge/02_reference/race/Triathlete Magazines Complete Triathlon Book The Training, Diet, Health, Equipment, and Safety Tips You Need to Do Your Best (Matt Fitzgerald).md`

## 工具脚本变化

- `prepare_second_layer.py`：生成第二层 reference 和 bike 工具卡。
- `seed_v2_vectors_from_v1.py`：按 `content_sha1` 复用 v1 里未变文本的 bge-m3 向量，避免全量重嵌入。
- `chunk_approved.py`：支持 `--include-reference`，并输出 `knowledge_tier` / `knowledge_type`。
- `vector_store.py`：默认切到 v2 bge-m3，并对 reference tier 做轻度降权。
- `rag_answer.py`：默认切到 v2 bge-m3；缺关键数据的补给计划走确定性安全 fallback。
- `eval_rag.py`：补强中文补给数字泄漏检查。

## v2 入库结果

chunker：

- documents: 30
- chunks: 12386
- approved chunks: 7795
- reference chunks: 4591

领域分布：

| domain | chunks |
|---|---:|
| bike | 60 |
| nutrition | 2027 |
| race | 3372 |
| recovery | 832 |
| run | 1689 |
| strength | 2650 |
| swim | 1756 |

向量库：

- `triathlon-knowledge/metadata/vectors/triathlon_core_v2_bge_m3.sqlite`
- model: `bge-m3:latest`
- vectors: 12386
- dim: 1024
- chunk links: 24712

向量构建时复用了 v1 中内容不变的向量：

- seeded from v1: 7011
- already current: 784
- newly embedded: 4591

## 验收结果

专项检索：

- `骑行 功率 周期训练 模板 周计划 强度 容量 peaking`：前两名命中 `骑行功率周期训练计划模板`。
- `FTP 193 平均功率 125 骑行 4小时 碳水消耗 热量计算`：前两名命中 `骑行热量与碳水消耗计算模板`。

回归测试：

- `python3 eval_rag.py --mode retrieval --json`：3/3 passed。
- `python3 eval_rag.py --mode answer --json`：3/3 passed。

## 后续建议

下一步不建议继续盲目扩大资料量。应该先做 bike 问答和课表生成的专项 eval：

1. FTP、IF、平均功率和时长的能量/碳水估算。
2. 8-12 周骑行功率周期训练模板检索。
3. 基础期、build、peak 的骑行课类型选择。
4. brick 训练和骑跑疲劳叠加。
5. 缺数据时拒绝直接生成课表，先追问目标赛事、FTP、近期负荷、可训练时间和恢复状态。

## Bike 专项 RAG 评测补强

本轮已完成上述 bike 专项 eval 的第一版，不再只依赖通用 `eval_rag.py`。

新增脚本：

- `eval_bike_rag.py`

覆盖用例：

- 骑行 4 小时、FTP 193、平均功率 125 的热量和碳水估算。
- 直接要求 8 周骑行功率课表时，缺少目标赛事、FTP、近期负荷和可训练时间则拒绝生成完整课表。
- 基础期骑行课型选择：Endurance Ride、Long Ride、Cadence Workout、Power Intervals、Threshold Ride。
- Brick 训练：明确 bike-run 组合、高强度计入周总强度，不能当普通耐力课叠加。

`rag_answer.py` 已增加确定性 bike 规则：

- 完整输入的骑行能量问题走本地公式计算，不交给模型自由发挥。
- 骑行功率课表请求只有在明确是“直接生成完整课表”时才触发缺数据拦截，避免把“课型原则”误判成排课请求。
- 基础期课型和 brick 高强度计数问题，召回到 bike 专章或 Triathlon Science 后走稳定的来源化回答。

同时修正了热量计算卡的机械功口径：

- Excel `G17` 对应每小时机械功，本例为 `450.0` kJ/h。
- 4 小时累计机械功为 `1800.0` kJ。

重新生成：

- `triathlon-knowledge/metadata/chunks/triathlon_core_v2_chunks.jsonl`
- `triathlon-knowledge/metadata/vectors/triathlon_core_v2_bge_m3.sqlite`

本次向量更新只重嵌入 1 个内容变更 chunk。

验收：

- `python3 -m py_compile rag_answer.py prepare_second_layer.py chunk_approved.py vector_store.py eval_bike_rag.py eval_rag.py`：通过。
- `python3 eval_bike_rag.py --mode answer --json`：4/4 passed。
- `python3 eval_bike_rag.py --mode retrieval --json`：4/4 passed。
- `python3 eval_rag.py --mode answer --json`：3/3 passed。

报告文件：

- `triathlon-knowledge/metadata/bike_rag_eval_answer_latest.json`
- `triathlon-knowledge/metadata/bike_rag_eval_retrieval_latest.json`
- `triathlon-knowledge/metadata/rag_eval_latest.json`
