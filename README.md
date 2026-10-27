# WraithWriter (bookforge)

WraithWriter is a command-line pipeline for planning, drafting and revising long fiction with LLMs. It runs these steps:

- brainstorm
- outline (with a structure linter)
- cast and world
- per-scene drafting from a story pack (bible, continuity sheet, recent chapters, voice sheets)
- critique
- a single editor pass
- de-AI checks
- canon extraction and continuity tracking

It keeps a cost and usage log for every call. The Python package is `bookforge`.

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

## The revision gate: code checks first, then Jev, then the expensive editor

The full-context editor pass is the costliest call in the pipeline. `screen.enabled` defaults to true, so `revise` screens each draft first and the editor reads a compact revision packet instead of the whole chapter plus the story pack. Set `screen.enabled: false` to send the whole chapter.

1. **Code checks (free)** run first for anything rule-checkable (`bookforge/core/screen_checks.py`):
   - clock times against the bible's schedule
   - which subject is taught in which session
   - afternoon-only activities
   - narration of a non-POV character's thoughts
   - new named people per scene
   - dialogue-line extraction for count rules such as "X answers only once"

   Regex checks for AI tells (rule of three, mirrored lines, epigram closers) only propose candidates.
2. **Jev** (TypeSafe System One, pinned to `jev-1.13.0`) makes one call per section. It only scores a fixed template of yes/no and choice questions against unstructured text: session-content conflicts, POV head-hops, mirrored lines, rule-of-three, tidy closers, age-appropriateness, and which planned beat the section shows. With `screen.deai_questions` (default true) that same call also asks six de-AI questions: packed noun, hedge stack, soft contrast, unclear agency, polished intro (first section of a scene only), and parallel-list ending (last section only). It also answers one yes/no per code-extracted line for each count rule, and code does the counting. Each question has its own threshold (`bookforge/jev_templates/chapter_section_screen.json`). A code candidate lowers the de-AI bars from 0.5 to 0.4. Jev never writes text. Calls are logged with their model, tokens and cost. Structural candidates (rule of three, mirrored lines, epigram closers, polished intro, parallel-list ending) are kept only when Jev agrees. If Jev is skipped, severity 2 and higher code hits stay.
3. **Revision packet** (`bookforge/core/screen.py`). It contains:
   - the flagged sections
   - the bible and plan lines that govern each flag
   - critique and canon findings, mapped to their sections
   - a beat map of the whole chapter

   The editor (`editor.edit_packet`) returns only the flagged sections. Code splices them back in, with length guards. If nothing is flagged, the editor call is skipped.
4. **Facts file**. Each chapter gets a `<chapter>-facts.json` next to its plan. It is generated from the outline, the characters, the bible timetable and the `screen` config. Keys you list in `_manual` (for example `count_rules`) are kept when the file is regenerated.

**Fallbacks.** If `TYPESAFE_API_KEY` is missing or the API errors, Jev is skipped, the code checks still run, and the packet is marked accordingly. A malformed editor reply falls back to the full-chapter edit.

Configuration (all optional):

```yaml
screen:
  enabled: true
  model: jev-1.13.0
  section_words: 600
  cap_usd: 1.0             # Jev spend cap per project
  skip_edit_if_clean: true
  fallback_full_edit: true
  facts_dir: plans         # where <chapter>-facts.json lives
  timetable_file: bible/schedule.txt
  max_new_per_scene: 2
```

To try it without calling the editor:

```bash
bookforge screen facts --project projects/myproject --chapter ch01
bookforge screen run   --project projects/myproject --chapter ch01 --no-jev
```

`screen run` writes `revision_packet.md` and `screen_estimate.json` under a new `steps/` folder. It never calls the editor. `--no-jev` is optional. `--text` reads a file instead of the draft.

On a 9,700-word test chapter, the packet cut editor input from about 62k to about 17.6k tokens (72% less). Jev cost under half a cent per chapter.

## De-AI rules

Fiction narration follows the style bible. That voice wins over any email register, and banned words and fragments are allowed inside quoted dialogue. The narrator does not get a "human slip" texture rule.

Where the rules live:

- `bookforge/style/deai_rules.md` is the short fiction list.
- `bookforge/style/vendor/de-ai-revision/rules_extract.md` is the vendored banlist, packed-noun, and sentence-rule extract.
- `bookforge/style/generated/deai_skill_banlist.txt` is generated from that extract.
- `bookforge/style/ai_tells.yaml` is the detector catalog, including `polished_intro` and `parallel_list_ending`.

`deai.rules_text()` joins the fiction list, the vendored extract, and a short fiction note. Writer and screen system prompts get a compact `PROSE RULES` block from `deai.prose_rules_block()`. The block is byte-stable for a book so it can sit in the cached prefix. `deai.prompt_rules` (default true) turns it off when false.

Sync the vendored extract and the banlist with `python tools/sync_style_rules.py`. The skill file it reads stays outside this public repo. `--check` rebuilds the banlist from the extract already in the tree and does not need that skill file.

Flags:

| Flag | Default | What it does |
|---|---|---|
| `deai.prompt_rules` | true | Put `PROSE RULES` in `pack.system_for` and `screen.lean_system` |
| `deai.min_severity` | 2 | Detector and lint hits sent to the editor |
| `deai.max_findings` | 40 | Cap on those located findings |
| `deai.edit_guard` | `section` | In a revision packet, revert a section whose detector score drops by more than `deai.max_tell_drop`. On a full-chapter edit, warn in the editor report. `off` disables both |
| `deai.max_tell_drop` | 0.5 | How far the 0-10 detector score may fall before the guard acts |
| `deai.rewrite_residual` | false | After the editor, rewrite paragraphs that still have a severity >= 2 narration hit, then re-scrub. The duties-gate rollback still applies. Off adds no LLM call |
| `deai.external_detector` | `provider: none` | Report-only hook after the final scrub. `pangram` reads the env var named by `api_key_env` (default `PANGRAM_API_KEY`) and records `skipped: no key` when it is unset. It never edits the chapter and never logs the key |
| `screen.enabled` | true | Code checks still run without `TYPESAFE_API_KEY`. Jev is skipped cleanly |
| `screen.deai_questions` | true | The six de-AI questions on the existing per-section Jev call |

## License

MIT. See `LICENSE`.
