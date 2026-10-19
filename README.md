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

The full-context editor pass is the costliest call in the pipeline. If `screen.enabled: true`, the `revise` step screens each draft first, and the editor reads a compact revision packet instead of the whole chapter plus the story pack.

1. **Code checks (free)** run first for anything rule-checkable (`bookforge/core/screen_checks.py`):
   - clock times against the bible's schedule
   - which subject is taught in which session
   - afternoon-only activities
   - narration of a non-POV character's thoughts
   - new named people per scene
   - dialogue-line extraction for count rules such as "X answers only once"

   Regex checks for AI tells (rule of three, mirrored lines, epigram closers) only propose candidates.
2. **Jev** (TypeSafe System One, pinned to `jev-1.13.0`) makes one call per section. It only scores a fixed template of yes/no and choice questions against unstructured text: session-content conflicts, POV head-hops, mirrored lines, rule-of-three, tidy closers, age-appropriateness, and which planned beat the section shows. It also answers one yes/no per code-extracted line for each count rule, and code does the counting. Each question has its own threshold (`bookforge/jev_templates/chapter_section_screen.json`). Jev never writes text. Calls are logged with their model, tokens and cost.
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

## License

MIT. See `LICENSE`.
