# Examples

A small, self-contained demo of the tri-coach RAG pipeline. Nothing here
depends on the private knowledge package — three short, original training
articles ship in `sample_knowledge/01_approved/` and the demo script wires
them through the project's normal ingest → embed → search path.

## Prerequisites

1. Python 3.10 or newer.
2. Install runtime deps:
   ```bash
   pip install -r requirements.txt
   ```
3. A local [Ollama](https://ollama.com/) server, with the embedding model pulled:
   ```bash
   ollama pull bge-m3
   ```

## Run

From the project root:

```bash
python3 examples/seed_demo.py
```

What the demo does:

1. Chunks the three sample articles into `examples/build/demo_chunks.jsonl`.
2. Embeds those chunks with `bge-m3` via Ollama into `examples/build/demo_vectors.sqlite`.
3. Runs a sample search against the resulting index and prints the top hits.

Custom query:

```bash
python3 examples/seed_demo.py --query "should I do brick workouts every week?"
```

Skip the rebuild step if the index already exists:

```bash
python3 examples/seed_demo.py --skip-build --query "..."
```

## Generating an Answer (Optional)

Once the demo index exists, you can also point the answer layer at it:

```bash
python3 rag_answer.py \
  --db examples/build/demo_vectors.sqlite \
  --provider ollama \
  --chat-model gemma2:latest \
  --question "Why does cardiac drift matter for a long-course triathlete?"
```

Use `--provider dry-run` if you only want to see the retrieval prompt without
calling a chat model.

## Notes

- The sample articles in `sample_knowledge/` are original, written for this
  demo, and released under the same MIT license as the rest of the repository.
  They are intentionally generic — they are not training prescriptions.
- The `examples/build/` directory is git-ignored; safe to delete any time.
