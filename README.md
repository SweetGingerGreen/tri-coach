# Triathlon Local RAG

本项目是一套本地铁三知识库和课表生成原型，目标是把已认可的训练知识、骑行/跑步/游泳计划规则、补给模板和教练计划逻辑，整理成可检索、可验证、可逐步扩展的本地 RAG 系统。

## What Is Tracked

- `chunk_approved.py`, `vector_store.py`, `rag_answer.py`: 本地知识库切块、向量存储和问答入口。
- `bike_plan_*.py`: 骑行课表、日程预检、长骑补给复核等生成/复核模块。
- `triathlon_plan_orchestrator.py`: 三项总课表协调器，保留骑行计划主体，并协调跑步、游泳、brick 和强度预算。
- `coach_plan_basa226_logic_check.py`: 用巴萨 226 备战计划提取出的设计逻辑做一致性校验。
- `eval_*.py`: 针对 RAG、骑行计划、三项协调器的本地评估脚本。
- `triathlon-knowledge/metadata/*.md|json`: 只跟踪轻量治理说明和逻辑 profile。
- `triathlon-knowledge/90_notes/*.md`: 阶段性入库、计划和使用说明。

## What Stays Local

以下内容默认不进入 Git：

- `triathlon-knowledge/00_inbox/`: 原始 inbox 文件、PDF、表格、OCR 输入。
- `triathlon-knowledge/01_approved/` 和 `02_reference/`: 已批准/参考知识正文。
- `triathlon-knowledge/metadata/chunks/` 和 `vectors/`: 切块文件与本地向量库。
- `triathlon-knowledge/metadata/*_latest.*`: 评估和生成过程报告。
- `.env*`, `venv/`, `venv_mineru/`, `.local_tools/`, `output/`: 本机环境、密钥、工具和临时产物。

这样 GitHub 仓库保存工程方法和可复用代码，个人资料、版权材料和本地向量库继续只留在本机。

## Quick Start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 -m py_compile *.py
python3 eval_triathlon_plan_orchestrator.py
python3 coach_plan_basa226_logic_check.py --write-output --write-review
```

OCR 和格式转换依赖外部工具时，按本机实际情况安装 `pandoc`、`ebook-convert` 或 MinerU 环境；这些工具不随仓库提交。

## GitHub Push

本地初始化和提交后，添加自己的 GitHub 远端再推送：

```bash
git remote add origin git@github.com:<owner>/<repo>.git
git push -u origin main
```
