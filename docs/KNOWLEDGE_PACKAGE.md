# Knowledge Package

`tri-coach` keeps source code in Git, but the private RAG knowledge base is **not**
distributed through public releases. It contains third-party training material
(books, course excerpts, historical coach plans) whose redistribution rights are
not held by this project. The package is shared privately, on request, only with
collaborators who already have rights to the underlying content.

## How To Request Access

Open a GitHub issue on this repository with the label `knowledge-access` and
briefly state:

- which collaborator role you're acting in (training partner, reviewer, etc.),
- which slice you need (`01_approved`, `02_reference`, vectors only, …),
- the rights you already hold to the underlying source material.

The maintainer will follow up out of band. Do not paste the package contents,
filenames, or hashes into public issues.

## Package Layout (private)

When provisioned, the package restores to:

```text
triathlon-knowledge/01_approved/
triathlon-knowledge/02_reference/
triathlon-knowledge/metadata/chunks/
triathlon-knowledge/metadata/vectors/
```

Transient SQLite sidecar files (`*.sqlite-wal`, `*.sqlite-shm`, `.DS_Store`) are
excluded from the package.

## Restore

From the project root on the collaborator machine, after receiving the tarball
out of band:

```bash
shasum -a 256 -c <package>.tar.gz.sha256
tar -xzf <package>.tar.gz
rsync -a <package>/triathlon-knowledge/ ./triathlon-knowledge/
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
python3 ironman_plan_logic_check.py --write-output --write-review
```

For RAG answer validation, point the answer layer at your local Gemma (or
OpenAI-compatible) endpoint via `TRI_RAG_PROVIDER` / `TRI_RAG_CHAT_BASE_URL` and
keep the restored vector/chunk paths unchanged unless the config is explicitly
updated.

## Public Users (No Private Package)

If you don't have access to the private package, run the demo pipeline in
[`examples/`](../examples/) instead — it ships with a small, original sample
corpus and exercises ingest → embed → retrieve → answer end-to-end.
