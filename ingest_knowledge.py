import os
import time
import opendataloader_pdf

print("==================================================")
print(" 🧠 铁三大脑 - 本地私有知识摄取引擎 (Data Ingestion) ")
print("==================================================")

# 设定收发文件夹
INBOX_DIR = "triathlon-knowledge/00_inbox"
OUTPUT_DIR = "triathlon-knowledge/00_inbox/processed"

if not os.path.exists(OUTPUT_DIR):
    os.makedirs(OUTPUT_DIR)

# 开始批量转换
print(f"\n📂 正在扫描 [{INBOX_DIR}] 下的隐秘级训练档案...")
start_time = time.time()

try:
    # 核心调用部分：剥壳提纯
    opendataloader_pdf.convert(
        input_path=[INBOX_DIR],
        output_dir=OUTPUT_DIR,
        format="markdown" # 只要 Markdown 格式，保证 RAG 极速切块
    )

    elapsed = time.time() - start_time
    print(f"\n✅ 转换全过程大获全胜！(耗时: {elapsed:.2f} 秒)")
    print(f"👉 纯净版 Markdown 资料已掉落在: {OUTPUT_DIR}")
    print("你现在可以随时把它们剪切分类到批准 (01_approved) 的知识库里了！")

except Exception as e:
    print(f"\n❌ 解析遭到阻断：\n{e}")
