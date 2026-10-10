# Progress

2026-10-10: User authorized directory and project path renaming. New root D:/Projects/YiceAgent; branding 忆策 Agent. Inventory started.

Owned HTTP stopped via PID/executable/creation-time checks. Baseline archives 197 original files and exact Git refs. Updated 82 source/docs files; namespace moved to src/yice_agent. DB snapshot first attempt failed read-only on psycopg percent placeholder; corrected to SQLAlchemy bound text, rerun before any infrastructure changes.

Windows root rename failed WinError32 because Codex/REPL/Ollama cwd handles pin it. Same-volume contents fallback then failed moving .git with WinError5; no project entries moved, new empty target rolled back. Diagnosing Git folder attributes before choosing an alternative.

User approved temporary shared Git metadata. Recursive same-volume move discovered a locked project-owned ollama-start.log; migration rolled back completely. Need restart only the previously project-started Ollama process (PID 17088, command serve, cwd/root/log confirmed), preserving D:/Ollma/models and process-local settings.

Migration succeeded after releasing only the verified project-started Ollama log. New root is canonical; old directory contains only shared Git metadata, core.worktree points to new root. 65 environment paths/launchers repaired, project editable metadata renamed without installing dependencies. Existing Ollama restored with original model directory/process-local host. Ruff,14-file mypy and doctor/GPU kernel PASS.

215 Python tests/0 skips,18 Node,889 historical blobs and five representative environment launchers PASS. First read-only real RAG query returned grounded/3 correct evidence but probe incorrectly expected evidence status; test assumption corrected against source contract, original result retained. Browser restored previous account conversation, UI branding updated. Final metadata remains PENDING by user-approved choice.

Final real GPU embedding+reranker PASS (1024 dimensions,8 candidates,3 grounded evidence). Public/private29 PG tables and3Qdrant collections identical, settings/OAuth bytes identical. HEAD/user branches/tags identical; raw all-ref comparison initially failed on application-managed temporary turn-diff refs, preserved and scoped correctly without restoring those refs. 81 Python AST comparisons differ only by approved namespace/branding/headers/environment keys. Actual cmd entrypoints from outside root both PASS. Five views and project OAuth readiness checked; no LLM generation/paid calls. 141 prior artifacts unchanged; amended current source mapping and 14 scoped artifacts, frozen evidence integrity PASS. Git metadata physical move remains deferred; finalizer check PENDING.
