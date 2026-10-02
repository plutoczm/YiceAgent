# Private Resource Findings

- Actual configs: Harness .env, Support .env, Analytics .env and docker/.env.production.
  Credential values are only read in-process, never printed or committed.
- Local DB files: Support supportops.db (~152 KiB) and Analytics Chroma SQLite
  (~356 KiB). Use SQLite backup API; preserve the whole stopped Chroma collection
  folder alongside a consistent SQLite snapshot and any original files.
- Named volumes: Harness PostgreSQL/Redis/Qdrant and Support PostgreSQL/Redis/Qdrant.
  Original containers are exited; create read-only cold archives with a locally
  existing image, no original startup/network/migration or volume deletion.
- Analytics original data/model folders are bind mounts, originals stopped.
  BGE-M3 and Qwen GGUF weights total ~4.5 GiB; retain existing local assets without
  downloads, training or model calls. Do not copy Conda/venv/build/test caches.
- Original source Git histories are separate; preserve local Git bundles privately
  for future recovery. No original repository commit/reset/fetch/push is authorized.
- WorkflowOps has 16 modified/510 old-path deletions/536 untracked entries from
  completed P6 and consolidation, empty index. User now authorizes local commit.
- Missing versioned original files are sample templates, .gitkeep placeholders,
  Analytics editor settings and a tracked real docker/.env.production. Real config
  must be archived privately even if the original mistakenly tracked it.
