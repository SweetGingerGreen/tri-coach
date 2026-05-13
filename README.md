# tri-coach

A local, self-hosted RAG system for triathlon training: it turns curated training
knowledge, bike/run/swim planning rules, fueling templates, and historical coach-plan
logic into a retrievable, verifiable, and incrementally extensible local pipeline.

[简体中文版本见下方](#中文版本)

---

> ## ⚠️ Safety Disclaimer
>
> `tri-coach` is an engineering tool, not a medical or training authority.
>
> - Outputs are **not** medical advice and **not** an individualized prescription.
> - If you have structural pain, joint instability, or any symptom that could
>   indicate injury or illness, see a qualified clinician — do not let any
>   automated tool override that.
> - Any training plan generated, reviewed, or critiqued by this code still
>   requires a human in the loop (you, or your coach) before it is acted on.
> - The repository ships with safety rails for medical-shaped questions
>   (see `triathlon-knowledge/metadata/00_brain_boundaries.md`); please leave
>   them in place if you fork.

---

## What's in the Repo

Code (MIT licensed):

- `chunk_approved.py`, `vector_store.py`, `rag_answer.py` — local chunking, vector
  store, and answer/generation entry point.
- `bike_plan_*.py` — cycling plan generation, daily preflight, long-ride
  fueling review, etc.
- `triathlon_plan_orchestrator.py` — triathlon-level orchestrator that keeps the
  cycling plan as the spine and coordinates run, swim, brick, and intensity
  budget.
- `ironman_plan_logic_check.py` — consistency check against a historical
  Ironman 226 coach-plan logic profile (logic only, not gold standard).
- `eval_*.py` — local evals for the RAG layer, bike plan modules, and the
  triathlon orchestrator.

Tracked governance / notes:

- `triathlon-knowledge/metadata/*.md|json` — lightweight governance notes
  and logic profiles.
- `triathlon-knowledge/90_notes/*.md` — staged ingest and usage notes.

## What Stays Local (and Why)

The following are intentionally **not** committed. They contain raw third-party
material (books, paid courses, historical coach plans) whose redistribution
rights this project does not hold:

- `triathlon-knowledge/00_inbox/` — raw inbox files, PDFs, sheets, OCR input.
- `triathlon-knowledge/01_approved/` and `02_reference/` — approved and
  reference knowledge bodies.
- `triathlon-knowledge/metadata/chunks/` and `vectors/` — chunked files and the
  local vector store.
- `triathlon-knowledge/metadata/*_latest.*` — eval and generation reports.
- `.env*`, `.venv/`, `.local_tools/`, `output/` — local environments,
  secrets, tools, and transient outputs.

The repository preserves the engineering methodology and reusable code; the
private corpus, the personal training data, and the local vector index remain
on the maintainer's machine.

## Quick Start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # then edit if you're not on default local Ollama
python3 -m py_compile *.py
```

To exercise the full ingest → embed → retrieve loop without any private data,
run the public demo:

```bash
ollama pull bge-m3
python3 examples/seed_demo.py
```

See [`examples/README.md`](examples/README.md) for details.

For generation (answer synthesis) you'll also want a chat model — locally that's
typically `ollama pull gemma2`, or you can point at any OpenAI-compatible
endpoint by setting `TRI_RAG_PROVIDER=openai-compatible` and the related env
vars in `.env`.

External tools like `pandoc`, `ebook-convert`, or MinerU are only needed if you
ingest non-Markdown sources, and are not bundled.

## Knowledge Sources

The private corpus is organized by training domain. Each domain holds a small,
curated set of textbooks, coaching guides, and reference works that the
maintainer already has rights to consult, used here as background material for
the RAG layer. Specific titles, authors, and source files are intentionally not
enumerated in this public repo — the categorical scope is:

- **race** — triathlon-level training science, periodization, race preparation.
- **run** — running physiology, training-load design, marathon and injury-prevention literature.
- **swim** — swim coaching frameworks, technical drills, sport-specific conditioning.
- **strength** — endurance-oriented strength training, concurrent training, periodization.
- **nutrition** — sports nutrition, hydration, dietary guidelines.
- **recovery** — anatomy references, soft-tissue and injury-prevention material.

None of those source materials are distributed by this project. The repository
ships only the engineering pipeline, the governance notes, and a small original
sample corpus under [`examples/`](examples/).

## Private Knowledge Package

For collaborators who already have rights to the underlying training content,
a private tarball can be provisioned out-of-band that restores:

```text
triathlon-knowledge/01_approved/
triathlon-knowledge/02_reference/
triathlon-knowledge/metadata/chunks/
triathlon-knowledge/metadata/vectors/
```

**It is not published as a release asset, and it is not freely shareable.**

To request access: open an issue on this repo with the label `knowledge-access`
and describe your role and the rights you already hold to the source material.
Procedure and restore instructions live in [`docs/KNOWLEDGE_PACKAGE.md`](docs/KNOWLEDGE_PACKAGE.md).

## License

Source code: [MIT](LICENSE).

The MIT license does **not** extend to any third-party training material that
may be referenced by, or restored into, this pipeline. Those works remain the
property of their respective owners.

---

<a id="中文版本"></a>

# 中文版本

`tri-coach` 是一套本地铁三知识库和课表生成原型。它把已认可的训练知识、骑行/跑步/游泳计划规则、补给模板和教练计划逻辑，整理成可检索、可验证、可逐步扩展的本地 RAG 系统。

## ⚠️ 安全声明

- `tri-coach` 是工程工具，**不是医疗建议，也不是个性化训练处方**。
- 任何涉及结构性疼痛、关节失稳或潜在伤病的表现，请去找合格的临床医师，不要让任何自动化工具替代它。
- 课表生成、复核或评估的产出，都必须在人工（你自己或你的教练）复核后才能采用。
- 仓库内置医疗类问题的拒答边界（见 [`triathlon-knowledge/metadata/00_brain_boundaries.md`](triathlon-knowledge/metadata/00_brain_boundaries.md)），fork 时请保留。

## 仓库内容

代码（MIT 协议）：

- `chunk_approved.py` / `vector_store.py` / `rag_answer.py`：本地切块、向量存储和问答入口。
- `bike_plan_*.py`：骑行课表、日程预检、长骑补给复核等生成/复核模块。
- `triathlon_plan_orchestrator.py`：三项总课表协调器，保留骑行计划主体，并协调跑步、游泳、brick 和强度预算。
- `ironman_plan_logic_check.py`：用一份历史 Ironman 226 教练计划提取出的设计逻辑做一致性校验（逻辑校验，不是黄金标准）。
- `eval_*.py`：针对 RAG、骑行计划、三项协调器的本地评估脚本。

跟踪的治理 / 笔记：

- `triathlon-knowledge/metadata/*.md|json`：轻量治理说明和逻辑 profile。
- `triathlon-knowledge/90_notes/*.md`：阶段性入库和使用说明。

## 默认不进入 Git 的内容

以下内容默认 **不进入** 公开仓库。它们涉及第三方版权材料（书籍、付费课程、历史教练方案）等本仓库不持有再分发权利的资料：

- `triathlon-knowledge/00_inbox/`：原始 inbox 文件、PDF、表格、OCR 输入。
- `triathlon-knowledge/01_approved/` 和 `02_reference/`：已批准 / 参考知识正文。
- `triathlon-knowledge/metadata/chunks/` 和 `vectors/`：切块文件与本地向量库。
- `triathlon-knowledge/metadata/*_latest.*`：评估和生成过程报告。
- `.env*`、`.venv/`、`.local_tools/`、`output/`：本机环境、密钥、工具和临时产物。

公开仓库只保留工程方法和可复用代码；个人训练数据、版权材料和本地向量库继续留在本机。

## 快速开始

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # 不用默认本地 Ollama 的话，按需修改
python3 -m py_compile *.py
```

想在不接触私有数据的情况下，跑一次完整的 入库 → 向量化 → 检索 流程，可以运行公开 demo：

```bash
ollama pull bge-m3
python3 examples/seed_demo.py
```

详见 [`examples/README.md`](examples/README.md)。

生成回答还需要一个 chat 模型。本地常用 `ollama pull gemma2`；如果想接 OpenAI 兼容端点，在 `.env` 里把 `TRI_RAG_PROVIDER` 设为 `openai-compatible` 并配好相关变量即可。

`pandoc` / `ebook-convert` / MinerU 等外部工具只在处理非 Markdown 源时需要，仓库不打包它们。

## 训练资料范围

私有语料按训练领域组织。每个领域只放维护者本人有权查阅的少量教练手册、运动科学教材和参考书，用作 RAG 层的背景资料。**具体书名、作者、文件名在本公开仓库中刻意不列出**，仅以类目说明覆盖范围：

- **race（铁三整体）**：铁三训练科学、周期化、比赛准备。
- **run（跑步）**：跑步生理、训练负荷设计、马拉松训练法、跑步损伤预防。
- **swim（游泳）**：游泳教练学框架、技术 drill、专项体能。
- **strength（力量）**：耐力导向力量训练、同期训练、周期化。
- **nutrition（运动营养）**：运动营养、补液补给、膳食指南。
- **recovery（恢复 / 解剖）**：解剖图谱、软组织和损伤预防资料。

本项目不分发上述任何源材料。仓库只包含工程流水线、治理笔记，以及 [`examples/`](examples/) 下一份小型原创演示语料。

## 私有知识包

如果协作者已经持有相关训练内容的使用权，我可以单独分发一份私有 tarball，恢复以下目录：

```text
triathlon-knowledge/01_approved/
triathlon-knowledge/02_reference/
triathlon-knowledge/metadata/chunks/
triathlon-knowledge/metadata/vectors/
```

**这份私有包不会作为 Release Asset 发布，也不会公开分享。**

申请方式：在本仓库开一个 issue 并打 `knowledge-access` 标签，说明你的角色，以及你对底层资料已经持有的使用权。恢复步骤见 [`docs/KNOWLEDGE_PACKAGE.md`](docs/KNOWLEDGE_PACKAGE.md)。

## 许可证

源代码：[MIT](LICENSE)。

MIT 协议 **不覆盖** 本流水线引用或还原的任何第三方训练材料，那些内容的著作权仍归原作者所有。
