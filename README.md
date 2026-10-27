# WraithWriter (bookforge)

WraithWriter is a command-line pipeline for planning, drafting and revising long fiction with LLMs. It runs these steps:

- brainstorm
- outline (with a structure linter)
- cast and world
- per-scene drafting from a story pack (bible, continuity sheet, recent chapters, voice sheets)
- critique
- a single editor pass
- an optional talkie reword (`talkie.enabled`, off by default)
- de-AI checks
- canon extraction and continuity tracking

It keeps a cost and usage log for every call. The Python package is `bookforge`.

## Setup

    python -m venv .venv && . .venv/bin/activate
    pip install -e ".[test]"
    cp .env.example .env     # fill in only the providers you use, then: set -a; . ./.env; set +a
    pytest -q

API keys are read only from environment variables:

| Variable | Used for |
|---|---|
| `XAI_API_KEY` | brainstorm role |
| `OPENROUTER_API_KEY` | default planner, drafter, editor and utility tiers |
| `ANTHROPIC_API_KEY` | optional |
| `TYPESAFE_API_KEY` | optional Jev screen (see below) |

Each provider's `api_key_env` names its variable. No key belongs in code or config files.

`bookforge config-init` writes a user config with model tiers and roles you can override. You can also point `BOOKFORGE_CONFIG` at a YAML file. Per-tier env overrides also work, for example `BOOKFORGE_EDITOR_MODEL` and `BOOKFORGE_DRAFTER_REASONING_EFFORT`.

## Usage

    bookforge init "A lighthouse keeper finds a message in a bottle" --project projects/myproject
    bookforge run --project projects/myproject --step all   # runs or resumes the pipeline
    bookforge run --project projects/myproject --step revise   # one step
    bookforge cost --project projects/myproject
    bookforge structure check --project projects/myproject

Project folders, manuscripts, state and logs are git-ignored.

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

    bookforge screen facts --project projects/myproject --chapter ch01
    bookforge screen run   --project projects/myproject --chapter ch01 [--no-jev]   # writes revision_packet.md and screen_estimate.json

On a 9,700-word test chapter, the packet cut editor input from about 62k to about 17.6k tokens (72% less). Jev cost under half a cent per chapter.

## Talkie reword

Off unless `talkie.enabled` is true. The pipeline step then sits after revise and before de-AI. It writes `manuscript/<id>_talkie.txt` and does not overwrite the draft or the revision. `bookforge talkie run` is an explicit opt-in even when the flag is false.

What the stage does, how to run the local server, and the experimental A/B numbers are in [docs/talkie.md](docs/talkie.md).

```yaml
talkie:
  enabled: false
  endpoint: http://127.0.0.1:8080
  n_rerolls: 4
  history_window: 2
  edit_budget: 0.15          # Sol may write at most max(3, this fraction of the paragraph)
  sol_tries: 2
  copy_max: 0.35             # source 4-gram share above this fails a candidate
  splice_copy_max: 0.5
  splice_seeds: 2
  dedupe_jaccard: 0.4
  jev_threshold: 0.5
  fewshot_path: null         # null uses the built-in synthetic few-shot
  temperature: 0.8
  min_p: 0.05
  repeat_penalty: 1.1
  timeout_s: 3600
  cap_usd: 0.50              # Jev spend cap for the run
  targeted:
    enabled: true            # only paragraphs that still drop a fact
    edit_budget: 0.25
    rounds: 1
```

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
| `talkie.enabled` | false | Opt-in reword. Off makes zero talkie, Jev or Sol calls. See the Talkie reword block and [docs/talkie.md](docs/talkie.md) |
| `screen.enabled` | true | Code checks still run without `TYPESAFE_API_KEY`. Jev is skipped cleanly |
| `screen.deai_questions` | true | The six de-AI questions on the existing per-section Jev call |

## License

MIT. See `LICENSE`.
