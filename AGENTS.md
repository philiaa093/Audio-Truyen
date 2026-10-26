# Repository Guidelines

## Project Structure & Module Organization

This workspace contains a local prototype for adapting long Vietnamese stories into shorter AI-generated audio.

- `frontend/`: HTML, CSS, and JavaScript for the editing workspace.
- `backend/server.py`: HTTP API, background tasks, and frontend asset serving.
- `backend/services/`: story editing pipeline and audio processing.
- `backend/providers.py`, `backend/config.py`, `backend/errors.py`: shared provider calls, paths, and errors.
- `tests/check.py`: runnable checks with local fake AI/TTS providers.
- `docs/deep-research-report.md`: the primary Vietnamese research report, including proposed architecture, TTS comparisons, quality criteria, prompts, and code examples.
- `docs/usage.md`, `docs/architecture.md`: operation, configuration, and workflow documentation.
- `storage/`: generated projects, logs, and temporary test data; excluded from Git.
- `AGENTS.md`: contributor instructions.

Treat the report's pipeline and snippets as proposals and examples, not proof that a capability is implemented. Keep related documentation in `docs/`. Use Python standard-library features and the existing plain frontend unless implementation requires more dependencies.

## Build, Test, and Development Commands

Run these commands from the workspace root in PowerShell:

- `rg --files --hidden`: list files before making changes.
- `Get-Content -LiteralPath .\docs\deep-research-report.md -Encoding UTF8 -TotalCount 80`: review the report's overview and assumptions.
- `rg -n '^#{1,6} ' .\docs\deep-research-report.md`: inspect heading candidates; matches can include comments inside code fences.
- `python -m backend`: start the local app at `http://127.0.0.1:8765`.
- `python -m tests.check`: run checks without paid provider calls.
- `python -m compileall -q backend tests`: check Python compilation.
- `node --check frontend/js/app.js`: check frontend JavaScript syntax.

No frontend build step, formatter, or linter is configured. Python 3.12+ is required; FFmpeg is needed for audio export. Keep setup and verification commands current in `README.md` and `docs/usage.md`.

## Coding Style & Naming Conventions

Save Markdown as UTF-8 and preserve Vietnamese diacritics. Maintain the report's Vietnamese prose, descriptive headings, fenced code blocks with language labels, and readable tables. Use lowercase, hyphen-separated names for new documents, such as `tts-evaluation.md`.

In examples, use four-space Python indentation and two-space JSON/YAML indentation. Keep configuration keys consistent with existing examples, including `target_duration`. Distinguish measured results from proposed defaults, and preserve source citations when editing factual claims.

## Testing Guidelines

Checks use the standard library; no third-party testing framework or coverage threshold is established. For documentation changes, preview Markdown and verify tables, diagrams, citations, and example consistency. Check time-sensitive provider claims against current sources.

For future pipeline changes, add a small runnable check with its invocation. Use the report's QA checklist for story fidelity, pronunciation, measured audio duration, and clipping; identify its numerical targets as pilot recommendations.

## Commit & Pull Request Guidelines

No Git metadata or history is present, so commit conventions cannot be inferred. When version control is introduced, use concise imperative subjects, for example `docs: clarify duration feedback loop`.

Pull requests should explain the change, reference relevant issues or sources, and record validation. Include rendered previews for layout changes and measured results for audio experiments.

## Security & Configuration

Keep provider credentials out of documents and examples. Use placeholders for secrets. Record usage rights for story text and voice samples before adding production material.
