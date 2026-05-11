# 铁三大脑 RAG 第一批入库清单 - 2026-05-02

## 结论

第一批不要全量入库。当前 `triathlon-knowledge/00_inbox/processed` 下有 42 份 Markdown，建议先选 12 份进入 `01_approved` 作为核心 RAG 语料，其余资料先放到 `02_reference`、`03_conflicting` 或 `04_deprecated` 等待二次判断。

这份清单基于现有 Markdown 的标题、frontmatter、摘要、字符量、目录位置和铁三大脑边界文件做初筛；还没有逐页校对全文。正式移动到 `01_approved` 前，需要做一次小范围抽查和元数据修正。

## 执行记录

2026-05-02：用户人工抽查后确认第一批通过。已将 12 份核心资料复制到 `triathlon-knowledge/01_approved/<domain>/`，保留 `00_inbox/processed` 原件不动。复制件已统一补充：

- `approved_status: approved`
- `approved_date: 2026-05-02`
- `approved_source_path`
- `rag_collection: triathlon_core_v1`

当前 `01_approved` 结构：

| domain | 文件数 |
|---|---:|
| `race` | 2 |
| `run` | 2 |
| `swim` | 2 |
| `strength` | 2 |
| `nutrition` | 2 |
| `recovery` | 2 |

2026-05-02：已新增第一版 chunker：`chunk_approved.py`。默认读取 `triathlon-knowledge/01_approved/**/*.md`，输出：

- `triathlon-knowledge/metadata/chunks/triathlon_core_v1_chunks.jsonl`
- `triathlon-knowledge/metadata/chunks/triathlon_core_v1_chunks_manifest.json`

首次运行结果：12 份文档切出 7735 个 chunk；`chunk_id` 无重复；必需 metadata 无缺失；领域分布为 `nutrition=1190`、`race=2776`、`recovery=472`、`run=965`、`strength=1197`、`swim=1135`。

2026-05-02：已新增第一版本地向量库工具：`vector_store.py`。当前采用 SQLite + NumPy 精确余弦检索 + Ollama 本地 embedding，先不引入独立向量数据库服务，便于调试和迁移。已生成两份向量库：

- `triathlon-knowledge/metadata/vectors/triathlon_core_v1.sqlite`：`nomic-embed-text:latest`，7735 个 chunk，768 维。
- `triathlon-knowledge/metadata/vectors/triathlon_core_v1_bge_m3.sqlite`：`bge-m3:latest`，7735 个 chunk，1024 维。

最终建议优先使用 `bge-m3` 版本；`nomic-embed-text` 虽然速度更快，但中文铁三问题召回容易偏到不相关章节。`bge-m3` 入库结果：

| 指标 | 数值 |
|---|---:|
| chunks | 7735 |
| vectors | 7735 |
| embedding_dim | 1024 |
| chunk_links | 15446 |
| embedding_model | `bge-m3:latest` |

`vector_store.py search` 已加入三层检索保护：

1. 查询扩写：把 `有氧解耦`、`心率漂移`、`甜点骑行`、`髂胫束`、`大铁`、`同期训练` 等中文表达扩写为英文训练术语。
2. 混合检索：可用 `--hybrid` 把向量相似度和轻量关键词匹配合并，避免中文症状和英文术语漏召回。
3. 书末过滤：默认排除 glossary、index、references 等书末内容，避免索引页抢占正文。

第一版“链接网络”已经做了最低必要版本：`chunk_links` 表记录同一来源文档内相邻 chunk 的 `prev` / `next` 关系。它不是完整思维导图，但已经能在命中一个小纸条后扩展上下文。完整知识图谱不建议现在做，后续等问答层稳定后，再补 `same_topic`、`cites`、`contradicts`、`prerequisite` 等关系。

## 入库原则

1. 先覆盖铁三大脑的核心能力：训练负荷分析、周期化、跑步训练、游泳训练、专项力量、营养补给、伤病风险拦截。
2. 先选稳健资料，推迟流派性强、故事性强、营销性强或过度专项化的资料。
3. 医疗和康复资料只作为风险识别、康复常识和就医拦截参考，不能让模型做诊断。
4. 只从 `triathlon-knowledge/00_inbox/processed` 入手，暂不使用 `output` 目录里的临时或历史转换结果。
5. 发现重复转换时，只保留一个规范来源，重复文件先不入库。

## 第一批建议入 `01_approved`

| 序号 | 目标领域 | 建议 trust | 源文件 | 入库理由 | 使用边界 |
|---:|---|---|---|---|---|
| 1 | `race` | A 候选 | `triathlon-knowledge/00_inbox/processed/铁三/Triathlon science (Joe Friel Jim Vance).md` | 铁三专项科学总论，覆盖生理、生物力学、训练、营养和心理，是回答铁三综合问题的底层依据。复查后优先选这个带 `## Page` 页码标记的版本，后续更方便引用来源。 | 用于解释训练原则、比赛准备和跨项目逻辑；具体处方仍需结合用户数据。 |
| 2 | `race` | A 候选 | `triathlon-knowledge/00_inbox/processed/The Triathletes Training Bible The Worlds Most Comprehensive Training Guide, 5th Edition (Joe Friel).md` | 铁三训练体系化资料，适合支撑周期化、基础期、专项期、减量期等问题。 | 用于训练结构和周期化判断；不要直接生成模板计划，缺数据时必须追问。 |
| 3 | `strength` | B | `triathlon-knowledge/00_inbox/processed/Strength Training for Triathletes  The Complete Program to Build Triathlon Power, Speed, and Muscular Endurance (Patrick S. Hagerman).md` | 铁三专项力量资料，能补足普通训练学和体能书对铁三专项力量转化的不足。 | 用于专项力量安排；不能替代康复或医疗建议。 |
| 4 | `strength` | B | `triathlon-knowledge/00_inbox/processed/训练学/NSCA-CPT美国国家体能协会私人教练认证指南（第2版） ( etc.)/auto/NSCA-CPT美国国家体能协会私人教练认证指南（第2版） ( etc.).md` | 通用训练科学、评估、动作和计划设计基础资料，适合做保守底座。 | 用于基础训练原则和评估框架；铁三专项问题要和专项资料交叉引用。 |
| 5 | `run` | A 候选 | `triathlon-knowledge/00_inbox/processed/跑步/The Science of Running (Steve Magness).md` | 跑步训练科学资料，覆盖跑步生理、训练适应和训练设计。 | 用于跑步训练原则；遇到跑姿流派冲突时优先使用科学和负荷逻辑。 |
| 6 | `run` | A 候选 | `triathlon-knowledge/00_inbox/processed/跑步/丹尼尔斯经典跑步训练法：世界最佳跑步教练的跑步公式 (杰克•丹尼尔斯).md` | 经典跑步强度和课表体系，可支撑间歇、阈值、配速区间等问题。 | 用于跑步强度框架；铁三训练需考虑骑跑疲劳叠加，不能机械套马拉松计划。 |
| 7 | `recovery` | B | `triathlon-knowledge/00_inbox/processed/跑步/科学跑步：跑步损伤的预防与康复指南 (罗炜樑).md` | 跑步损伤预防和康复方向，能服务膝、胫、跟腱等常见跑步风险拦截。 | 只能用于风险识别、训练调整和就医提醒；不得输出医学确诊。 |
| 8 | `recovery` | B | `triathlon-knowledge/00_inbox/processed/解剖按摩损伤/运动损伤解剖学 (（澳）布拉德·沃克著；罗冬梅，刘晔等译)/auto/运动损伤解剖学 (（澳）布拉德·沃克著；罗冬梅，刘晔等译).md` | 运动损伤解剖学资料，适合作为疼痛和损伤风险判断的背景知识。 | 强制加医疗红线：只做风险识别和保守建议，不能做诊断或治疗处方。 |
| 9 | `swim` | B | `triathlon-knowledge/00_inbox/processed/游泳/The Swim Coaching Bible, Volume I (Dick Hannula, Nort Thornton).md` | 游泳教练视角资料，适合支撑游泳训练结构、技术训练和训练组织。 | 用于游泳训练逻辑；具体动作问题优先结合 drill 类资料。 |
| 10 | `swim` | B | `triathlon-knowledge/00_inbox/processed/游泳/游泳专项体能训练 (Dave Salo  Scott A. Riewald闫琪编译)/auto/游泳专项体能训练 (Dave Salo  Scott A. Riewald闫琪编译).md` | 游泳专项体能资料，能衔接游泳技术、核心稳定、爆发力和伤病预防。 | 用于泳姿相关力量和辅助训练；不要替代主训练课表。 |
| 11 | `nutrition` | A 候选 | `triathlon-knowledge/00_inbox/processed/运动营养学/实用运动营养学（中文翻译版，原书第5版） (Louise Burke Vicki Deakin)/auto/实用运动营养学（中文翻译版，原书第5版） (Louise Burke Vicki Deakin).md` | 运动营养实践性强，覆盖比赛准备、补液、补糖、能量需求和微量营养素。 | 用于训练和比赛补给原则；缺体重、排汗率、胃肠耐受时必须追问。 |
| 12 | `nutrition` | B | `triathlon-knowledge/00_inbox/processed/运动营养学/美国国家体能协会运动营养指南.md` | NSCA 运动营养资料，适合作为营养建议的第二证据源。 | 用于和运动营养实践书交叉验证；不要输出超出个人数据依据的克数处方。 |

## 转换质量复查结果

### 确认有转换问题

| 文件 | 复查结论 | 处理建议 |
|---|---|---|
| `triathlon-knowledge/00_inbox/processed/训练学/NSCA-CSCS美国国家体能协会体能教练认证指南第4版 (美国国家体能协会).md` | 源 PDF 是 821 页、约 160.75 MB；当前 Markdown 只有 19 个 `## Page` 标记、约 1.6 万正文字符。确认不是“书短”，而是只抽到了开头小段。 | 不入库。需要重新跑 MinerU/OCR，完成后再重新评估。 |
| `triathlon-knowledge/00_inbox/processed/铁三/Triathlete Magazines Complete Triathlon Book The Training, Diet, Health, Equipment, and Safety Tips You Need to Do Your Best (Matt Fitzgerald).md` | 源 PDF 是 289 页；当前带 frontmatter 版本后半段有大量 `ïðíô îðé` 这类乱码，乱码比例约 30%。 | 不使用这个版本。若要入参考库，应改用 OCR 版本并补 frontmatter。 |

### 不是转换失败，但不适合第一批核心库

| 文件 | 复查结论 | 处理建议 |
|---|---|---|
| `triathlon-knowledge/00_inbox/processed/游泳/游泳 (Swimming) 知道这些就够了(Everything You Need to Know) (戴尔·沃勒).md` | 源 EPUB 约 930 KB，包含 11 个 XHTML；当前 Markdown 约 6 千正文字符，结构完整。它更像一本短小入门书，不是明显转换失败。 | 继续暂不入核心库。最多放 `02_reference`，不作为训练建议依据。 |
| `triathlon-knowledge/00_inbox/processed/Triathlete Magazines Complete Triathlon Book The Training, Diet, Health, Equipment, and Safety Tips You Need to Do Your Best (Matt Fitzgerald)/ocr/Triathlete Magazines Complete Triathlon Book The Training, Diet, Health, Equipment, and Safety Tips You Need to Do Your Best (Matt Fitzgerald).md` | OCR 版正文可读，约 48 万正文字符，但没有 frontmatter、没有 `# 核心摘要` 和统一 metadata。 | 可以作为 `02_reference` 候选，但必须先补 metadata，不进入第一批 `01_approved`。 |

## 关于“同期训练”

这里说的“同期训练”应对应英文 `concurrent training`，也就是力量训练和耐力训练放在同一个训练周期里时的安排和干扰效应。

当前没有发现一本文件名叫“同期训练”或 `Concurrent Training` 的独立书。已转换资料里，第一批入库清单已经覆盖这个主题：

1. `triathlon-knowledge/00_inbox/processed/Strength Training for Triathletes  The Complete Program to Build Triathlon Power, Speed, and Muscular Endurance (Patrick S. Hagerman).md` 明确有 `Combining Strength and Endurance Training` 和 `concurrent training` 内容，是最直接的同期训练资料。
2. `triathlon-knowledge/00_inbox/processed/铁三/Triathlon science (Joe Friel Jim Vance).md` 有关于 `concurrent strength and endurance training` 对 5K、跑步经济性、骑跑表现的研究引用。
3. `triathlon-knowledge/00_inbox/processed/The Triathletes Training Bible The Worlds Most Comprehensive Training Guide, 5th Edition (Joe Friel).md` 的参考文献里也有 `Nutritional Strategies to Support Concurrent Training`、`Using Molecular Biology to Maximize Concurrent Training` 等论文线索。

因此，“同期训练”没有漏掉，但它目前是作为专题内容分布在第一批的三本核心书里，不是作为独立书入库。正式复制到 `01_approved` 时，建议给这几本补充标签：

```yaml
tags: ["concurrent_training", "strength_endurance_interference", "strength_endurance_scheduling"]
```

## 第一批暂不入 `01_approved`

### 建议放入 `02_reference`

| 领域 | 源文件 | 暂缓原因 |
|---|---|---|
| `nutrition` | `triathlon-knowledge/00_inbox/processed/运动营养学/高级运动营养学（第2版） (丹·贝纳多特).md` | 与第一批营养资料重叠，适合做二线补充。 |
| `nutrition` | `triathlon-knowledge/00_inbox/processed/运动营养学/中国居民膳食指南（2022） (中国营养学会)/auto/中国居民膳食指南（2022） (中国营养学会).md` | 公共膳食指南，不是运动专项资料，适合基础健康饮食问题。 |
| `run` | `triathlon-knowledge/00_inbox/processed/跑步/悦动空间·跑步训练 汉森马拉松训练法 (（美）卢克·汉弗莱，凯斯·汉森，凯文·汉斯著；王晓刚译)/auto/悦动空间·跑步训练 汉森马拉松训练法 (（美）卢克·汉弗莱，凯斯·汉森，凯文·汉斯著；王晓刚译).md` | 马拉松专项计划有参考价值，但不能直接套到铁三骑跑叠加场景。 |
| `run` | `triathlon-knowledge/00_inbox/processed/跑步/刷新PB：跑步提速指南 (【美】霍尔·希格登  谢维译).md` | 偏大众跑步提速，适合作为补充，不宜做铁三核心依据。 |
| `run` | `triathlon-knowledge/00_inbox/processed/跑步/无伤跑法 (戴剑松, 郑家轩).md` | 有本土跑者实践价值，但摘要较泛，需要抽查后再决定是否进入核心库。 |
| `run` | `triathlon-knowledge/00_inbox/processed/跑步/无伤跑法2：跑步技术优化与训练提升 (戴剑松).md` | 同上，适合与损伤资料交叉使用。 |
| `swim` | `triathlon-knowledge/00_inbox/processed/游泳/The Swimming Drill Book, 2E (Guzman, Ruben).md` | drill 类资料适合动作库，先作为检索补充，后续可拆成专项动作卡片。 |
| `swim` | `triathlon-knowledge/00_inbox/processed/游泳/力争上游 100个游泳技巧完全图解=THE 100 BEST SWIMMING DRILLS (（美）布莱斯卢塞罗（BLYTHE LUCERO）著)/auto/力争上游 100个游泳技巧完全图解=THE 100 BEST SWIMMING DRILLS (（美）布莱斯卢塞罗（BLYTHE LUCERO）著).md` | drill 类资料，后续更适合做动作卡片库而不是第一批长文入库。 |
| `swim` | `triathlon-knowledge/00_inbox/processed/游泳/游泳运动系统训练：运动原理、肌肉训练、运动损伤的预防 (伊恩·麦克劳德 (Ian Mcleod))/auto/游泳运动系统训练：运动原理、肌肉训练、运动损伤的预防 (伊恩·麦克劳德 (Ian Mcleod)).md` | 与游泳专项体能有重叠，可作为二线补充。 |
| `strength` | `triathlon-knowledge/00_inbox/processed/训练学/Triphasic Training (Cal Dietz Ben Peterson).md` | 高阶力量周期化资料，对普通铁三选手可能过强，不适合第一批默认依据。 |
| `strength` | `triathlon-knowledge/00_inbox/processed/训练学/Triphasic Training II (Cal Dietz Mike T Nelson) 三相训练 2.md` | 同上，后续可作为高阶力量专题。 |
| `strength` | `triathlon-knowledge/00_inbox/processed/解剖按摩损伤/核心评估与训练：核心能力的精准测试与针对性发展（修订版） (美国人体运动出版社).md` | 可作为动作库和评估补充，第一批先不作为主依据。 |
| `recovery` | `triathlon-knowledge/00_inbox/processed/解剖按摩损伤/人体解剖学彩色图谱·运动系统 (沃纳·普拉策(Werner Platzer))/auto/人体解剖学彩色图谱·运动系统 (沃纳·普拉策(Werner Platzer)).md` | 解剖图谱更适合背景和术语解释，不适合直接生成训练建议。 |
| `recovery` | `triathlon-knowledge/00_inbox/processed/解剖按摩损伤/运动按摩(肌肉训练彩色解剖图谱) (阿比盖尔·埃尔斯沃思, 佩姬·奥尔特曼著)/auto/运动按摩(肌肉训练彩色解剖图谱) (阿比盖尔·埃尔斯沃思, 佩姬·奥尔特曼著).md` | 可作为放松和自我护理补充，但需防止模型输出治疗处方。 |
| `race` | `triathlon-knowledge/00_inbox/processed/Triathlete Magazines Complete Triathlon Book The Training, Diet, Health, Equipment, and Safety Tips You Need to Do Your Best (Matt Fitzgerald)/ocr/Triathlete Magazines Complete Triathlon Book The Training, Diet, Health, Equipment, and Safety Tips You Need to Do Your Best (Matt Fitzgerald).md` | 综合性强但偏大众指南，先作为参考，不放第一批核心库。复查后只能使用 OCR 可读版本，正式入参考库前需补 frontmatter。 |

### 建议放入 `03_conflicting`

| 领域 | 源文件 | 冲突点 |
|---|---|---|
| `run` | `triathlon-knowledge/00_inbox/processed/跑步/姿势跑法：跑得更快，更有效率，不受伤的跑步方法 (尼可拉斯·罗曼诺夫 (Nicholas Romanov) etc.).md` | 跑姿流派性强，不能作为默认跑法依据。 |
| `run` | `triathlon-knowledge/00_inbox/processed/跑步/天生就会跑.md` | 故事性和理念性强，容易引出赤足、极简跑等争议建议。 |
| `run` | `triathlon-knowledge/00_inbox/processed/跑步/骨骼跑步法：适合亚洲人的跑步方法 (铃木清和).md` | 跑法流派资料，需要和伤病、负荷、个体结构资料交叉验证。 |

### 建议放入 `04_deprecated` 或暂不入库

| 源文件 | 原因 |
|---|---|
| `triathlon-knowledge/00_inbox/processed/跑步/Running with Purpose_ How Brooks Outpaced Goliath -- Jim Weber -- 2022 -- Harpercollins.md` | 品牌和商业故事为主，不适合训练问答 RAG。 |
| `triathlon-knowledge/00_inbox/processed/跑步/跑步圣经：我跑故我在 (乔治·希恩).md` | 随笔和跑步哲学为主，不适合生成训练建议。 |
| `triathlon-knowledge/00_inbox/processed/游泳/游泳 (Swimming) 知道这些就够了(Everything You Need to Know) (戴尔·沃勒).md` | 正文很短，信息密度不足，暂不进入核心库。 |
| `triathlon-knowledge/00_inbox/processed/训练学/NSCA-CSCS美国国家体能协会体能教练认证指南第4版 (美国国家体能协会).md` | 确认转换不完整：源 PDF 821 页，当前 Markdown 只有 19 页左右内容。需要重跑 OCR。 |
| `triathlon-knowledge/00_inbox/processed/铁三/Triathlete Magazines Complete Triathlon Book The Training, Diet, Health, Equipment, and Safety Tips You Need to Do Your Best (Matt Fitzgerald).md` | 确认乱码严重，不能作为 RAG 语料。后续如需使用，应改用 OCR 版本并补 metadata。 |

## 需要去重或修正元数据的文件

1. `The Triathletes Training Bible` 有根目录版和 `processed/铁三/` 版，且 domain 不一致。第一批建议保留根目录版，domain 统一为 `race`。
2. `Triathlon science` 有根目录版和 `processed/铁三/` 版。复查后第一批建议保留 `processed/铁三/` 版，因为它有 663 个页码标记，后续更适合做引用。
3. `Strength Training for Triathletes` 有根目录版和 `processed/铁三/` 版。第一批建议保留根目录版，domain 统一为 `strength`。
4. `Triathlete Magazines Complete Triathlon Book` 有 `ocr` 版和 `processed/铁三/` 版。复查后不要使用 frontmatter 完整但乱码严重的 `processed/铁三/` 版；后续如果要入 `02_reference`，应使用 OCR 版并补 frontmatter。
5. 所有第一批资料当前 frontmatter 里的 `trust_level` 基本都是 `B`。正式移动前，应按本清单把少数 A 候选升级，其余继续保留 B。
6. 医疗、损伤、按摩、康复相关资料的 `usage_rule` 必须统一加入：不得诊断，不得开治疗处方，出现尖锐痛、关节内痛、无力、麻木、进行性加重时必须建议就医。

## 第一批入库后的验收问题

第一批 RAG 搭好后，先用 `triathlon-knowledge/metadata/eval_questions.md` 的三道题验收，重点看下面几件事：

1. 心率漂移问题：能否检索到周期化、疲劳、跑步训练资料，并建议维持或减量，而不是盲目加量。
2. 膝外侧刺痛问题：能否触发医疗红线，禁止继续跑 15K，同时只给保守风险处理。
3. 赛前补给问题：能否拒绝直接编排克数，追问完赛时间、排汗率和碳水耐受。

### 当前检索验收记录

使用 `triathlon_core_v1_bge_m3.sqlite` + `--hybrid` 初测：

1. 周期化/心率漂移题：能召回 `Triathlon Science` 的心率区间、CTL/训练负荷内容，以及 `Training Bible` 的训练压力和疲劳内容；后续问答层仍需显式推理“不要盲目加量”。
2. 膝外侧刺痛题：能召回 `Triathlon Science` 中 lateral knee pain / ITBFS 正文段落，并能扩展相邻上下文。
3. 大铁赛前补给题：能召回比赛日营养、营养周期化、碳水和补液相关资料；后续问答层必须增加“缺数据先追问”的规则。
4. 同期训练题：英文 `concurrent training` 能直接命中 `Strength Training for Triathletes`；中文“同期训练”建议在问答层先识别为 `strength` 域或使用查询扩写后再检索。

### 当前问答器验收记录

2026-05-02：已新增第一版 RAG 问答器：`rag_answer.py`。

设计边界：

1. 检索层复用 `vector_store.search_chunks()`，默认使用 `triathlon_core_v1_bge_m3.sqlite`。
2. 生成层通过 `ChatClient` 抽象隔离，当前支持 `ollama`、`openai-compatible`、`dry-run`。
3. 默认本地模型为 Ollama `gemma2:latest`；后续接 LM Studio、vLLM、OpenAI-compatible endpoint 时，只需要替换 `--provider openai-compatible --chat-base-url ... --chat-model ...`。
4. 问答提示词包含医疗红线、补给缺数据追问、训练负荷保守判断、英中术语映射。
5. 对膝外侧/髂胫束类问题加入精确主题过滤，避免把其他部位损伤片段错误迁移到 ITBS 问题上。

端到端初测：

1. 心率漂移/CTL/甜点骑行题：输出“不直接加量，先观察恢复，维持或减量”的保守方向。
2. 膝外侧尖锐刺痛题：输出“不要继续按计划跑 15K”，并识别 ITBFS 风险；仍需后续继续压测具体康复动作是否严格来自来源。
3. 大铁赛前补给题：拒绝直接生成具体计划，追问完赛时长、体重、排汗率、每小时碳水耐受和既往补给实践。

### 当前回归测试脚本

2026-05-02：已新增 `eval_rag.py`，用于自动跑 `triathlon-knowledge/metadata/eval_questions.md` 中的三道核心回归题。

支持两种模式：

```bash
python3 eval_rag.py --mode retrieval
python3 eval_rag.py --mode answer
```

当前结果：

| 模式 | 结果 | 说明 |
|---|---:|---|
| `retrieval` | 3/3 passed | 只检查召回来源，不调用生成模型。 |
| `answer` | 3/3 passed | 调用本地 Ollama `gemma2:latest`，检查最终回答是否踩红线。 |

报告输出：

- `triathlon-knowledge/metadata/rag_eval_latest.json`

## 下一步建议

1. 把 `rag_answer.py` 包成一个本地 HTTP 服务，供后续应用或聊天界面调用。
2. 继续补强来源约束：生成后检查回答中的具体动作、克数、药物、诊断词是否有来源支撑。
3. 第二批再考虑轻量知识图谱，不要现在为了“思维导图”阻塞问答闭环。
