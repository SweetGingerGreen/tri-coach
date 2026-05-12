# Knowledge Package

`tri-coach` keeps source code in Git, while the private RAG knowledge base is distributed as a separate release asset.

## Current Package

- Package: `tri-coach-knowledge-2026-05-12-v1.tar.gz`
- Manifest: `tri-coach-knowledge-2026-05-12-v1.manifest.json`
- SHA256: `6aa3438e1da6cc6c6cdb9a068074398b417a4f74908565eabc0c92bc6fd756a3`
- Uncompressed payload: about `324.5 MB`
- Compressed asset: about `136 MB`
- File count: `37`

## Included Data

The package contains the private data needed for collaborator-side RAG validation:

```text
triathlon-knowledge/01_approved/
triathlon-knowledge/02_reference/
triathlon-knowledge/metadata/chunks/
triathlon-knowledge/metadata/vectors/
```

It excludes transient SQLite sidecar files:

```text
*.sqlite-wal
*.sqlite-shm
.DS_Store
```

## Restore

From the project root on the collaborator server:

```bash
shasum -a 256 -c tri-coach-knowledge-2026-05-12-v1.tar.gz.sha256
tar -xzf tri-coach-knowledge-2026-05-12-v1.tar.gz
rsync -a tri-coach-knowledge-2026-05-12-v1/triathlon-knowledge/ ./triathlon-knowledge/
```

After restore, the project should have:

```text
./triathlon-knowledge/01_approved/
./triathlon-knowledge/02_reference/
./triathlon-knowledge/metadata/chunks/
./triathlon-knowledge/metadata/vectors/
```

## Validate

```bash
python3 -m py_compile *.py
python3 eval_triathlon_plan_orchestrator.py
python3 coach_plan_basa226_logic_check.py --write-output --write-review
```

For RAG answer validation, point the answer layer at the collaborator's Gemma service and keep the restored vector/chunk paths unchanged unless the config is explicitly updated.
