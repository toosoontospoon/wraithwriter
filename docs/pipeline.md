# Pipeline

Step order is `STEP_ORDER` in `bookforge/core/progress.py`. `bookforge run --step all` runs a step only when its input hash is stale, unless `--force` is set.

| Order | Step | Scope | What it does |
|---|---|---|---|
| 1 | `brainstorm` | book | Optional web research on the xAI responses API (`brainstorm.web_research`, default true; skipped when `init --no-research` set the project), cached in `state/web_research.json` for the same premise. The search uses the xAI provider and the brainstorm model's name. Then two brainstorm-role calls: themes and genre, then an author persona. Saved on `metadata.brainstorm`. |
| 2 | `outline` | book | Six cached phases under `state/outline_work/`: structure, cast, world, per-chapter scene expansion, assessment, then the craft plan (`state/craft/ledger.json`) when `craft.enabled` is true. Chapter ids are `ch01` onward. Length is estimated from beats and is not an input. |
| 3 | `initialize_cast_and_world` | book | Deepens the existing cast and world (merge, does not replace ids) and seeds the canon ledger. |
| 4 | `write` | chapter | One drafter call per beat group. Groups are split so a scene stays within `context.scene_max_words_per_call` (6000). With `write.require_human_seed` (default true) the first scene must open on a human seed file; the model continues after that text and does not rewrite it. Each scene is resumed from `progress.scenes` if that scene file already exists. Optional `craft.rerank` (default false) adds a second ending plus a judge call when tension is at least 7. |
| 5 | `critique` | chapter | Deterministic checks (names, POV, AI tells), thread obligations, and craft rules, plus one planner-tier critique. Saved at `state/critiques/<id>.json`. |
| 6 | `revise` | chapter | One editor pass. With `screen.enabled` (default true) the editor sees a revision packet. Then canon re-extraction when `canon.reextract_after_revise` is true. `voice.revoice` is not called from this step. `voice.enabled` defaults to false. |
| 7 | `deai` | chapter | Fiction-mode token scrub and detector autofix. No LLM call unless `deai.rewrite_residual` is true. Then a duties gate (thread obligations plus craft rules) that can roll back to the autofixed draft, a final scrub, and a report-only external detector. |
| 8 | `audit` | book | Deterministic canon checks, then one planner-tier read of the canon digest and chapter summaries. |
| 9 | `finalize` | book | Assembles the manuscript from de-AI'd chapters and asks for a title and blurb. Refuses to run when chapters are unfinished or the audit is missing or failed, unless `bookforge run --step finalize --force`. |

`write`, `critique`, `revise`, and `deai` are the chapter steps. The others run once for the book.

## Human seed

The write step refuses to draft a chapter when `write.require_human_seed` is true (the default) and the first scene has no non-whitespace seed. The chapter file is `<project>/manuscript/<chapter_id>/human_seed.md`. A scene file `<project>/manuscript/<chapter_id>/sceneK/human_seed.md` (`scene1` or `scene01`) replaces it for that scene. The chapter seed is used only for the first scene.

The file is plain UTF-8 markdown. Optional YAML front matter (`---` … `---`) is removed, including the newline after the closing `---`. The body is not otherwise edited. Trailing whitespace at the very end of the body is left off the join; the continuation is appended immediately after the remaining prefix. Scrub and `clean_prose` run on the continuation only. The scene draft and the chapter draft both start with that prefix. Craft scrub inside the write step is not allowed to replace the prefix. `protect.py` cannot cheaply mark the seed for later edit/revise passes without turning exact-line protection on for unrelated lines (`norm()` also folds punctuation). That later-pass gap is a known limit.

A TTY may paste a seed (a line that is only `.`, or EOF). It is saved to the chapter seed path. A non-TTY gets an error that names the path and the format. No prompt.

Raw model text, still unscrubbed, plus the seed prefix:

```
bookforge run --project P --step write --chapter ch13 --raw-dir DIR
```

That also writes `DIR/ch13-v1-gemma-raw.txt` (then `v2`, …). The family is `gemma` when the write-role model id contains `gemma`. Scene breaks match `join_scenes`. `write.raw_dir` is the same directory when the flag is omitted. The config-init template points this drafter at Gemma on the Hugging Face router (32k context; USD per 1M input 0.48, cached 0.10, output 1.44). Talkie stays an opt-in fallback reword (`talkie.enabled` false), not the default writer.

`revise.recritique` and `revise.max_passes` are in `DEFAULT_CONFIG` and are not read by the revise step. The editor pass count is one.

## Models

Tiers live under `llm.models`. A role in `llm.roles` either names `provider` and `model` or points at a `tier`. Env overrides `BOOKFORGE_<TIER>_PROVIDER`, `BOOKFORGE_<TIER>_MODEL`, and `BOOKFORGE_<TIER>_REASONING_EFFORT` apply only to tier roles.

| Tier | Default provider | Default model | Reasoning effort | Headroom |
|---|---|---|---|---|
| planner | openrouter | `openai/gpt-6-sol` | medium | 4000 |
| drafter | openrouter | `openai/gpt-6-sol` | medium | 6000 |
| editor | openrouter | `openai/gpt-6-sol` | high | 24000 |
| utility | openrouter | `google/gemini-2.5-flash` | none set | 0 |

| Role | Target |
|---|---|
| brainstorm | xAI `grok-4.7` (not a tier) |
| outline, worldbuild, character_design, world_design, plan, craft_plan, critique, audit | planner |
| write, revision, rerank | drafter |
| edit | editor |
| deai, craft_extract | utility |
| summary, state_update, extract, deconflict | planner |
| default_role | summary |

Prices used when the provider does not return a cost are `llm.prices` (USD per 1M tokens). See [config.md](config.md). Jev is priced in code at $0.042 per 1M input tokens. Output tokens are free. That price is not a config key.

## Screen

`revise` calls `bookforge.core.screen.run` when `screen.enabled` is true.

1. Code checks (`screen_checks.run`), then detector hits at `deai.min_severity` (`deai_candidates`). No model call.
2. Jev, model `screen.model` (default `jev-1.13.0`), one call per section. It returns probabilities, not prose. A separate call scores each code-extracted line when the facts file has `count_rules`. Those rules start empty. Code counts lines whose yes-probability is above 0.5.
3. The editor (`editor.edit_packet`) rewrites flagged sections. Code splices them back. Length must stay between half and 1.6 times the original section.

Code checks that run on every chapter:

- Clock times, only when the facts file has timetable lines or `screen.event_times`. Timetable lines are loaded only from `screen.timetable_file`. The default is empty, so nothing is read until you set it. `bible/schedule.txt` is not a built-in path.
- Session subject and afternoon-only, only when `screen.subjects`, `screen.session_subjects`, or `screen.afternoon_only` is non-empty. All three default empty.
- Non-POV cognition (`Name felt|thought|…`) for first names on stage who are not the POV. Those names are generated from the outline.
- Regex candidates: rule of three, mirrored openings, epigram closers. Also hedge-stack and packed-noun candidates in narration.
- New named people. A scene is flagged `too_many_new_people` only when `screen.max_new_per_scene` is greater than 0 and the scene exceeds it. The default is 0, so the check records names and does not flag.

With Jev up, structural candidates (`rule_of_three`, `mirrored`, `epigram_closer`, `polished_intro`, `parallel_list_ending`) stay only when the matching Jev question agrees. A code hit on a linked check lowers that question from `flag_at` 0.5 to `flag_with_code_at` 0.4 where the template defines it (`bookforge/jev_templates/chapter_section_screen.json`). If Jev is skipped, severity 2 and higher code hits stay, and regex hits that have no severity stay too.

Jev questions on each section: `session_content_conflict`, `pov_head_hop`, `ai_mirrored_lines`, `ai_rule_of_three`, `ai_tidy_closer`, `ya_appropriate`, and `plan_beat` (a choice). When `screen.deai_questions` is true (the default), the same call also asks `ai_packed_noun`, `ai_hedge_stack`, `ai_soft_contrast`, `ai_unclear_agency`, `ai_polished_intro` (first section of a scene only), and `ai_parallel_list_ending` (last section of a scene only).

The editor is skipped only when `screen.skip_edit_if_clean` is true (default) and the packet has no flagged sections and no chapter-level findings. Chapter-level findings with nothing to splice send the full chapter instead. A packet-editor failure falls back to the full-chapter edit when `screen.fallback_full_edit` is true (default). `screen.enabled: false` skips the screen and edits the whole chapter. `screen.jev: false` or `screen run --no-jev` keeps the code checks and skips Jev (`jev` status `skipped: disabled`). A missing `TYPESAFE_API_KEY` or a Jev error sets `skipped: <reason>`. Spend at `screen.cap_usd` (default 1.0, from `state/jev_calls.jsonl`) also skips further Jev calls.

Facts file: `<chapter>-facts.json`. With `screen.facts_dir` empty, the directory is `<bible dir>/book<N>/` when `metadata.book` is set and that directory exists, otherwise `<project>/state/facts/`. It is not `plans/`. Regeneration rewrites generated keys and keeps every top-level key named in `_manual`. A new file sets `_manual` to `["count_rules"]`.

`bookforge screen facts` and `bookforge screen run` never call the editor. See [cli.md](cli.md).

## Structure linter

`bookforge structure check|show|percent`. Rules, fields, and exit codes are in `STRUCTURE.md`. `check` exits 1 on any FAIL: missing `plot_function`, missing or unchanged `value_turn`, or a `plot_function` that duplicates a neighbour.

## De-AI and the token scrub

Narration follows the style bible. That voice overrides any email register. The fiction note says not to add a human-slip texture rule. Banned words and fragments are allowed inside quoted dialogue and not in narration.

Files:

- `bookforge/style/deai_rules.md` is the short fiction list.
- `bookforge/style/vendor/de-ai-revision/rules_extract.md` is the vendored banlist, packed-noun, and sentence-rule extract.
- `bookforge/style/generated/deai_skill_banlist.txt` is generated from that extract.
- `bookforge/style/ai_tells.yaml` is the detector catalog.

`deai.rules_text()` joins those two files and the fiction note. `deai.prose_rules_block()` returns a fixed `PROSE RULES` block when `deai.prompt_rules` is true, and an empty string when it is false. The block is the same bytes for every call so it can sit in the cached system prefix.

The scrubber in this repo is `bookforge/style/vendor/token_scrub/scrub.py`. There is no separate fiction-safe script. Fiction mode is `scrub(text, mode="fiction")` in that file.

The pipeline does not shell out. `bookforge.core.deai.scrub` loads that file by path and calls `scrub(text, mode="fiction")`. `deai.dash_policy` defaults to `fiction`. The function always passes `mode="fiction"` and does not read the config value. `scrub_report` uses the same mode and returns the vendor's count dict. `detector.autofix` and `bookforge detect --fix` go through `deai.scrub`, so they are fiction mode too.

The file is also a CLI. Its default mode is prose, which is the upstream dash-to-period rules, not the pipeline:

```bash
python3 bookforge/style/vendor/token_scrub/scrub.py --mode fiction CHAPTER.txt -o out.txt --report
python3 bookforge/style/vendor/token_scrub/scrub.py --mode prose DRAFT.txt -i --report
```

Flags: optional input path (stdin if omitted), `-o`/`--output`, `-i`/`--inplace`, `--report` (counts on stderr), `--mode prose|fiction` (default `prose`).

Fiction mode, from the vendored file: a number range around a dash becomes `10-12`; a dash before a closing quote becomes `...`; a dash right after an opening quote is removed; a dash at end of line becomes `.`; ASCII `--` becomes `, `; other em/en dashes become `, `; a tight figure dash, horizontal bar, or minus between word characters becomes `-`. Then ellipsis becomes `...`, smart quotes including `„` and `‚` become straight quotes, and NBSP and zero-width characters are stripped or turned into spaces. Prose mode does not rewrite `„` or `‚`, and its remaining dashes become `. `.

`python tools/sync_style_rules.py` copies three sections of an external skill file into `rules_extract.md` and regenerates the banlist. `--check` rebuilds the banlist from the extract already in the tree and does not need the skill file. `--scrub PATH` records the upstream scrubber's sha256 in `bookforge/style/vendor/VENDORED.json` and prints when it drifts. It does not copy those bytes. Fiction mode is a local patch. The manifest says prose-mode output stays byte-identical to upstream.

`deai.external_detector.provider` defaults to `none` (status `skipped`). `pangram` checks that the variable named by `api_key_env` (default `PANGRAM_API_KEY`) is set. If it is unset, status is `skipped: no key`. If it is set, status is `skipped: not implemented`. The HTTP call is not in this tree. The hook never edits the chapter and never logs the key.

`deai.rewrite_residual` defaults to false. The de-AI step then makes no LLM call. When true, flagged narration paragraphs are rewritten on the utility tier (`role` `deai`) and scrubbed again. `deai.max_flag_ratio` is in the config and has no reader outside the test that it exists.

## Cost

Three mechanisms are in the code.

Screen skip. A clean packet does not call the editor. Jev is capped per project at `screen.cap_usd` (default $1). Code checks are free. The order is the rule in `bookforge/core/jev.py`: code for anything a rule can check, Jev only for a fixed yes/no or choice question on unstructured text, and a full LLM only for open-ended writing or judgment. There is no router that picks an LLM model by price at call time. The utility tier is the cheap model, and it is not the default for summary, state, extract, or deconflict (those roles use the planner tier).

Caching. `llm.cache.enabled` defaults to true. `openai_explicit` defaults to true, so only breakpoints are cached for `openai/` models (`openai_ttl` default `30m`). An empty `cache.key` becomes `bookforge:` plus the project directory name, taken from the `costs.jsonl` path. Builders insert `<<<CACHE_BREAK>>>`. The system prompt is always a breakpoint, with at most four breakpoints. `bookforge cost` reports cached tokens and hit rate from `state/usage.jsonl`.

Other skips that avoid a repeat call: outline phases cached in `state/outline_work/`, craft plans cached unless `bookforge craft plan --force`, and scene drafts skipped when `progress.scenes` already marks them written. `embeddings.enabled` defaults to false, so the vectors extra does no work until you turn it on. `craft.rerank` defaults to false.

`llm.budget_usd` defaults to null. Set it to raise `BudgetExceeded` before the next call once `costs.jsonl` spend has reached the number. It does not finish the scene that was in progress.
