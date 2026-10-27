# WraithWriter (bookforge)

WraithWriter is a command-line pipeline for planning, drafting and revising long fiction with LLMs. It runs these steps:

- brainstorm
- outline (each chapter gets a `structure` block; lint it with `bookforge structure check`)
- cast and world
- per-scene drafting from a story pack (bible, continuity sheet, recent chapters, voice sheets)
- critique
- a single editor pass
- de-AI checks
- canon extraction and continuity tracking

It keeps a cost and usage log for every call. The Python package is `bookforge`.

Stages, models, the screen, and the token scrub: [docs/pipeline.md](docs/pipeline.md). Every `DEFAULT_CONFIG` key: [docs/config.md](docs/config.md). Commands: [docs/cli.md](docs/cli.md). Structure fields: [STRUCTURE.md](STRUCTURE.md). Experiments: [docs/experiments/README.md](docs/experiments/README.md).

## Direction

Current work explores base-model drafting and rewording with `talkie-1930-13b-base`, then a budgeted fact fix. That stage is in progress in its own pull request on branch `talkie-rolling`. It is not in this tree, and its config is not documented here.

## Setup

Requires Python 3.10+. The installable package is `bookforge` (console script `bookforge`, also `python -m bookforge.cli`).

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[test]"
cp .env.example .env     # fill in only the providers you use
set -a; . ./.env; set +a
python -m pytest -q
```

The code never reads `.env` by itself. Export the variables, or source the file as above. Do not print a key.

Optional extras in `pyproject.toml`:

| Extra | Packages | When |
|---|---|---|
| `test` | pytest | `python -m pytest` |
| `webui` | streamlit | `streamlit run webui.py` |
| `tokens` | tiktoken | Exact token counts. Without it, counts are `len(text) // 4`. |
| `vectors` | sentence-transformers, numpy | Dense retrieval, and only if `embeddings.enabled` is true (default false). |

Core dependencies are click, requests, pydantic, and pyyaml.

### Environment

API keys come from the variable named by each provider's `api_key_env`. No key belongs in code or in the YAML config.

| Variable | Read by | Used for |
|---|---|---|
| `XAI_API_KEY` | `llm.providers.xai` | Brainstorm role (`grok-4.7` on xAI). Not a tier, so the `BOOKFORGE_*` tier overrides do not change it. |
| `OPENROUTER_API_KEY` | `llm.providers.openrouter` | Default provider for the planner, drafter, editor, and utility tiers. |
| `ANTHROPIC_API_KEY` | `llm.providers.anthropic` | Only when a tier's provider is `anthropic`. |
| `TYPESAFE_API_KEY` | `bookforge/core/jev.py` | Optional Jev screen. Without it, code checks still run and Jev is skipped. |
| `PANGRAM_API_KEY` | `deai.external_detector` | Only when that block's `provider` is `pangram`. The default provider is `none`, so this variable is not read. |
| `BOOKFORGE_CONFIG` | `config.config_path` | YAML merged over `DEFAULT_CONFIG`. Unset means `~/.bookforge/config.yaml`. |
| `BOOKFORGE_<TIER>_PROVIDER` | `resolve_role` | Tiers: `PLANNER`, `DRAFTER`, `EDITOR`, `UTILITY`. Overrides that tier's provider. |
| `BOOKFORGE_<TIER>_MODEL` | `resolve_role` | Overrides that tier's model. |
| `BOOKFORGE_<TIER>_REASONING_EFFORT` | `role_params` | `low`, `medium`, `high`, or `none` (omit the reasoning body). |
| `BOOKFORGE_PROTECT_EXACT_LINES` | `protect.exact_enabled` | `1`, `true`, `yes`, or `on` turns on exact `KEEP` / `SUBTEXT` line protection. Off by default. `FACT` / `EVENT` hints stay on without it. |

`bookforge config-init` writes a key-free copy of the defaults to `$BOOKFORGE_CONFIG` or `~/.bookforge/config.yaml`, and only if that file does not already exist. `bookforge run --edit-effort low|medium|high` sets `BOOKFORGE_EDITOR_REASONING_EFFORT` for that process.

Ollama is a provider with an empty `api_key_env` (`http://localhost:11434/v1`). It needs no key.

### Project files

```bash
bookforge init "A lighthouse keeper finds a message in a bottle" --project projects/myproject
```

`--words` is accepted and ignored. `--no-research` skips web research during brainstorm. With no `--project`, the directory is `projects/<slug>_<timestamp>`.

A project holds `metadata.json`, `progress.json`, `costs.jsonl`, `threads.yaml`, `steps/`, `state/` (`outline.json`, `characters.json`, `world.json`, `summaries.json`, canon, `state/facts/`, `state/craft/ledger.json`, `state/usage.jsonl`), and `manuscript/` (`<id>_draft.txt`, `<id>_revised.txt`, `<id>_final.txt`). Style files, when you add them, are `style_bible.md` and `voices/*` in the project directory, or in `style.bible_dir` / `metadata.style_bible_dir`.

`.gitignore` ignores `projects/`, `project/`, `*-project/`, `manuscript/`, `manuscripts/`, `state/`, `logs/`, `output/`, `outputs/`, `*.log`, and `*.jsonl` anywhere in the tree.

## Usage

Full flags, aliases, and subcommands: [docs/cli.md](docs/cli.md).

```bash
bookforge run --project projects/myproject --step all
bookforge run --project projects/myproject --step revise --chapter ch03
bookforge run --project projects/myproject --from critique --to deai --chapter ch03
bookforge run --project projects/myproject --restart-from revise --step revise --chapter ch03
bookforge cost --project projects/myproject
bookforge structure check --project projects/myproject
```

`--step` names, in order: `brainstorm`, `outline`, `initialize_cast_and_world`, `write`, `critique`, `revise`, `deai`, `audit`, `finalize`, or `all`. `write`, `critique`, `revise`, and `deai` are per chapter. `--from` and `--to` are an inclusive range and must be given together. `--restart-from` marks that step and everything after it stale, archives stale chapter files when the restart is at or before `write`, and does not run anything unless you also pass `--step` or a range.

`bookforge cost` reports `<project>/state/usage.jsonl` (default grouping: chapter). `bookforge costs` totals `<project>/costs.jsonl` by role. Set `llm.budget_usd` to stop before the next call once spend reaches the cap. The default is no cap. The process does not complete a leftover scene after the cap or after an out-of-credit HTTP error. Those errors surface as `BudgetExceeded` or `LLMError`.

## Screen, structure, de-AI

`screen.enabled` defaults to true. `revise` runs free code checks, then one Jev call per section on the pinned model `jev-1.13.0`, then sends the editor a packet of flagged sections. The editor call is skipped only when nothing is flagged and there are no chapter-level findings (`screen.skip_edit_if_clean`, default true). A missing `TYPESAFE_API_KEY` skips Jev and keeps the code checks.

Timetable, session-subject, and afternoon-only checks do nothing until `screen.timetable_file`, `screen.subjects`, `screen.session_subjects`, or `screen.afternoon_only` is set. Those default empty. `screen.facts_dir` defaults empty, which writes `<chapter>-facts.json` under `state/facts/`, or under `<bible>/book<N>/` when `metadata.book` is set and that directory exists. `screen.max_new_per_scene` defaults to 0, which records new names and does not flag them.

```bash
bookforge screen facts --project projects/myproject --chapter ch01
bookforge screen run   --project projects/myproject --chapter ch01 --no-jev
bookforge structure check --project projects/myproject
```

`structure check` exits 1 on a missing `plot_function`, a `value_turn` that does not change, or a `plot_function` that duplicates a neighbour. `structure show` and `structure percent --write` are in [STRUCTURE.md](STRUCTURE.md).

The de-AI step scrubs with fiction mode inside `bookforge/style/vendor/token_scrub/scrub.py`, loaded by `bookforge.core.deai.scrub`. There is no separate fiction-safe script. The file's own CLI defaults to `--mode prose`. `deai.rewrite_residual` defaults to false, so that step adds no LLM call. `deai.external_detector.provider` defaults to `none`. `pangram` does not call the network in this tree: a missing key is `skipped: no key`, and a set key is `skipped: not implemented`.

Prompt caching is on (`llm.cache.enabled`, default true). `llm.budget_usd` is the only spend stop, and it defaults to no cap. Detail is in [docs/pipeline.md](docs/pipeline.md) and [docs/config.md](docs/config.md).

## License

MIT. See `LICENSE`.
