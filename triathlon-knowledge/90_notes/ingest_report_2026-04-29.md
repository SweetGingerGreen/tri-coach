# 铁三资料本地转换报告 - 2026-04-29

## 本次处理方式

- 源目录：`triathlon-knowledge/00_inbox/铁三`
- 输出目录：`triathlon-knowledge/00_inbox/processed`
- Markdown 模板：已套用 `title/domain/source/trust_level/language/author/date/tags/usage_rule` frontmatter，以及 `# 核心摘要 (Summary)` / `# 正文内容 (Content)`
- 本地工具：`pandoc` 转 EPUB，`pypdf` 快速抽取文本型 PDF
- OCR/电子书回退：`MinerU` 本地 API + `ebook-convert`
- 本地模型：Ollama `gemma2:latest` 生成元数据、标签和中文摘要
- 未上传外部服务

## 最终结果（2026-04-30 续跑完成）

- 源文件：40 条
- 缺失输出：0 条
- `processed` Markdown：42 份健康 / 0 份需复核
- `failed_needs_review`：0 份 Markdown
- 旧失败残留已归档到：`triathlon-knowledge/00_inbox/resolved_failed_archive/2026-04-30`
- 续跑时为提升稳定性，MinerU 使用本地 API、`ch_lite` OCR，并关闭公式/表格结构识别；正文 OCR 与图片引用均保留。

## 初始结果（2026-04-29 快速批处理）

- 审计健康 Markdown：28 份
- 快速批处理异常：14 条
- 异常原因：
  - 2 条 AZW3 需要 Calibre 的 `ebook-convert`
  - 12 条 PDF 是扫描/图谱/图片型资料，`pypdf` 抽不到足够正文，需要单独跑 MinerU/OCR

## 已解决：Calibre AZW3

- `triathlon-knowledge/00_inbox/铁三/Strength Training for Triathletes  The Complete Program to Build Triathlon Power, Speed, and Muscular Endurance (Patrick S. Hagerman).azw3`
- `triathlon-knowledge/00_inbox/铁三/铁三/Strength Training for Triathletes  The Complete Program to Build Triathlon Power, Speed, and Muscular Endurance (Patrick S. Hagerman).azw3`

## 已解决：OCR 回退 PDF

- `triathlon-knowledge/00_inbox/铁三/游泳/力争上游 100个游泳技巧完全图解=THE 100 BEST SWIMMING DRILLS (（美）布莱斯卢塞罗（BLYTHE LUCERO）著).pdf`
- `triathlon-knowledge/00_inbox/铁三/游泳/游泳专项体能训练 (Dave Salo  Scott A. Riewald闫琪编译).pdf`
- `triathlon-knowledge/00_inbox/铁三/游泳/游泳运动系统训练：运动原理、肌肉训练、运动损伤的预防 (伊恩·麦克劳德 (Ian Mcleod)).pdf`
- `triathlon-knowledge/00_inbox/铁三/解剖按摩损伤/人体解剖学彩色图谱·运动系统 (沃纳·普拉策(Werner Platzer)).pdf`
- `triathlon-knowledge/00_inbox/铁三/解剖按摩损伤/解剖列车：徒手与动作治疗的肌筋膜经线 (托马斯·梅尔斯 (Thomas W.Myers)).pdf`
- `triathlon-knowledge/00_inbox/铁三/解剖按摩损伤/运动按摩(肌肉训练彩色解剖图谱) (阿比盖尔·埃尔斯沃思, 佩姬·奥尔特曼著).pdf`
- `triathlon-knowledge/00_inbox/铁三/解剖按摩损伤/运动损伤解剖学 (（澳）布拉德·沃克著；罗冬梅，刘晔等译).pdf`
- `triathlon-knowledge/00_inbox/铁三/解剖按摩损伤/骨盆和骶骼关节功能解剖 手法操作指南 详解局部解剖和功能 涵盖评估分析 运动 肌肉能量技术及替代技 (（英）约翰·吉本斯，（John Gibbons）著；朱....pdf`
- `triathlon-knowledge/00_inbox/铁三/训练学/NSCA-CPT美国国家体能协会私人教练认证指南（第2版） ( etc.).pdf`
- `triathlon-knowledge/00_inbox/铁三/跑步/悦动空间·跑步训练 汉森马拉松训练法 (（美）卢克·汉弗莱，凯斯·汉森，凯文·汉斯著；王晓刚译).pdf`
- `triathlon-knowledge/00_inbox/铁三/运动营养学/中国居民膳食指南（2022） (中国营养学会).pdf`
- `triathlon-knowledge/00_inbox/铁三/运动营养学/实用运动营养学（中文翻译版，原书第5版） (Louise Burke Vicki Deakin).pdf`

## 复用命令

单本 OCR：

```bash
PYTHONUNBUFFERED=1 venv_mineru/bin/python ingest_knowledge_v2.py ingest --path '<PDF路径>' --metadata --model gemma2:latest --ocr-fallback
```

整批 OCR：

```bash
PYTHONUNBUFFERED=1 venv_mineru/bin/python ingest_knowledge_v2.py ingest --path 'triathlon-knowledge/00_inbox/铁三' --recursive --metadata --model gemma2:latest --ocr-fallback
```
