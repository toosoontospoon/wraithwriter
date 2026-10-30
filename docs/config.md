# Config reference

Source: `DEFAULT_CONFIG` in `bookforge/config.py`. A user file (`$BOOKFORGE_CONFIG`, or `~/.bookforge/config.yaml`) is deep-merged over this. `bookforge config-init` writes a key-free user template only when that file is missing. The template is `example_config()`, not a verbatim dump of the library defaults: library planner, drafter, and editor stay on `openrouter` + `openai/gpt-6-sol` (drafter reasoning `medium`, headroom 6000) and utility stays on `google/gemini-2.5-flash`. The template points `write` and `rerank` at Gemma and the other generative roles at DeepSeek flash. See the ready-to-copy block below.

Values below are the defaults. One line says what the code does with the key. A key that nothing outside the defaults reads is marked unused.

Per-tier environment overrides, applied at resolve time for roles that use a tier: `BOOKFORGE_<TIER>_PROVIDER`, `BOOKFORGE_<TIER>_MODEL`, `BOOKFORGE_<TIER>_REASONING_EFFORT` (`low`, `medium`, `high`, or `none`). Tiers are `PLANNER`, `DRAFTER`, `EDITOR`, `UTILITY`.

## `llm`

| Key | Default | Meaning |
|---|---|---|
| `providers.xai.kind` | `openai` | OpenAI-compatible chat adapter. |
| `providers.xai.base_url` | `https://api.x.ai/v1` | xAI base URL. Web search posts to `{base_url}/responses`. |
| `providers.xai.api_key_env` | `XAI_API_KEY` | Env var for this provider. |
| `providers.openrouter.kind` | `openai` | OpenAI-compatible chat adapter. |
| `providers.openrouter.base_url` | `https://openrouter.ai/api/v1` | OpenRouter base URL. Requests set `usage.include`. |
| `providers.openrouter.api_key_env` | `OPENROUTER_API_KEY` | Env var for this provider. |
| `providers.anthropic.kind` | `anthropic` | Native Messages adapter (`/messages`), with an explicit cache breakpoint on the system text. |
| `providers.anthropic.base_url` | `https://api.anthropic.com/v1` | Anthropic base URL. |
| `providers.anthropic.api_key_env` | `ANTHROPIC_API_KEY` | Env var for this provider. |
| `providers.ollama.kind` | `openai` | Local OpenAI-compatible server. |
| `providers.ollama.base_url` | `http://localhost:11434/v1` | Ollama base URL. |
| `providers.ollama.api_key_env` | empty | No key is read. |
| `providers.huggingface.kind` | `openai` | OpenAI-compatible chat adapter. Requests go to `{base_url}/chat/completions`. |
| `providers.huggingface.base_url` | `https://router.huggingface.co/v1` | Hugging Face router. OpenRouter-only cache fields (`usage.include`, `prompt_cache_key`, `session_id`, `prompt_cache_options`) are not sent. |
| `providers.huggingface.api_key_env` | `HF_TOKEN` | Env var for this provider. `Authorization: Bearer <HF_TOKEN>`. |
| `models.planner.provider` | `openrouter` | High-intelligence tier. |
| `models.planner.model` | `openai/gpt-6-sol` | Default planner model. |
| `models.planner.extra_body` | reasoning effort `medium` | Overridden by `BOOKFORGE_PLANNER_REASONING_EFFORT`. |
| `models.planner.max_tokens_multiplier` | `1.0` | Sent `max_tokens` is requested times this, plus headroom. |
| `models.planner.max_tokens_headroom` | `4000` | Added to the planner `max_tokens`. |
| `models.drafter.provider` | `openrouter` | Prose tier. |
| `models.drafter.model` | `openai/gpt-6-sol` | Default drafter model. |
| `models.drafter.extra_body` | reasoning effort `medium` | Overridden by `BOOKFORGE_DRAFTER_REASONING_EFFORT`. |
| `models.drafter.max_tokens_multiplier` | `1.0` | Same formula as the planner. |
| `models.drafter.max_tokens_headroom` | `6000` | Added to the drafter `max_tokens`. |
| `models.editor.provider` | `openrouter` | Full-context editor tier. |
| `models.editor.model` | `openai/gpt-6-sol` | Default editor model. |
| `models.editor.extra_body` | reasoning effort `high` | Overridden by `BOOKFORGE_EDITOR_REASONING_EFFORT` or `run --edit-effort`. |
| `models.editor.max_tokens_multiplier` | `1.0` | Same formula as the planner. |
| `models.editor.max_tokens_headroom` | `24000` | Added to the editor `max_tokens`. |
| `models.utility.provider` | `openrouter` | Cheap bookkeeping tier. |
| `models.utility.model` | `google/gemini-2.5-flash` | Default utility model. No `extra_body`. |
| `models.gemma.provider` | `huggingface` | Optional prose tier. Library `write` / `rerank` stay on `drafter` until a user config points them here. |
| `models.gemma.model` | `nbeerbower/Gemma4-Gutenberg-31B-Heretic:featherless-ai` | Id sent on the wire, including the `:featherless-ai` suffix. |
| `models.gemma.max_tokens_headroom` | `1024` | No reasoning `extra_body`. On this 32k window the write step also skips its +8000 pad, and a larger headroom in the role is clamped to 1024. |
| `models.gemma.context_tokens` | `32768` | 32k context. A write pack over ~24k tokens is trimmed with `context.total_context_tokens` and `block_caps` (the existing `truncate_tokens` path). |
| `roles.brainstorm` | provider `xai`, model `grok-4.7` | Direct model. Tier env vars do not apply. |
| `roles.outline` | tier `planner` | Outline calls. |
| `roles.worldbuild` | tier `planner` | Worldbuilding role. |
| `roles.character_design` | tier `planner` | Cast deepening. |
| `roles.world_design` | tier `planner` | World deepening. |
| `roles.plan` | tier `planner` | Planning role. |
| `roles.craft_plan` | tier `planner` | Craft planners. |
| `roles.critique` | tier `planner` | Chapter critique. |
| `roles.audit` | tier `planner` | Book audit. |
| `roles.write` | tier `drafter` | Scene drafting. |
| `roles.revision` | tier `drafter` | Targeted paragraph fixes. The chapter editor uses `edit`. |
| `roles.edit` | tier `editor` | The one editor pass. |
| `roles.deai` | tier `utility` | Residual paragraph rewrite, only when `deai.rewrite_residual` is true. |
| `roles.rerank` | tier `drafter` | Optional second-ending judge. |
| `roles.summary` | tier `planner` | Summaries. Also `default_role`. |
| `roles.state_update` | tier `planner` | State updates. |
| `roles.extract` | tier `planner` | Fact extraction. |
| `roles.craft_extract` | tier `utility` | Craft extraction. |
| `roles.deconflict` | tier `planner` | Canon conflict judge. |
| `default_role` | `summary` | Fallback when a role name is missing, with a warning. |
| `prices.moonshotai/kimi-k3` | input 0.73, output 10.0, cache_read 0.55 | USD per 1M tokens. Not the default model. |
| `prices.z-ai/glm-5.3` | input 0.079, output 4.8, cache_read 0.059 | USD per 1M tokens. Not the default model. |
| `prices.openai/gpt-6-sol` | input 2.0, output 10.0, cache_read 0.2, cache_write 2.5 | Estimate when the provider omits cost. |
| `prices.grok-4.7` | input 2.0, output 6.0 | Estimate when the provider omits cost. |
| `prices.google/gemini-2.5-flash` | input 0.3, output 2.5, cache_read 0.03, cache_write 0.383 | Estimate when the provider omits cost. |
| `prices.nbeerbower/Gemma4-Gutenberg-31B-Heretic:featherless-ai` | input 0.48, cache_read 0.10, output 1.44 | USD per 1M. Featherless page, routed through HF with no markup. The router returns `usage` without `cost`, so the log estimates from this row (`cost_source: estimate`) and still records `latency_s`. The price key is the full model id. |
| `cache.enabled` | `true` | Insert cache breakpoints at `<<<CACHE_BREAK>>>` and on the system prompt. |
| `cache.key` | empty | Empty becomes `bookforge:` plus the project directory name. |
| `cache.openai_explicit` | `true` | For `openai/` models, cache only those breakpoints. |
| `cache.openai_ttl` | `30m` | TTL sent with explicit OpenAI caching. |
| `cache.ttl` | empty | If set, added to Anthropic `cache_control` when the model id starts with `anthropic/`. |
| `budget_usd` | `null` | No cap. A number raises `BudgetExceeded` before the next call once spend reaches it. |
| `timeout` | `180` | HTTP timeout in seconds for chat calls. Web search uses 120. |
| `http_retries` | `4` | Attempts on network errors, HTTP 429, and HTTP 5xx. |
| `json_retries` | `2` | Extra attempts after a JSON parse failure (three tries total). |

## `embeddings`

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `false` | Dense vectors. Off uses BM25 only. Needs the `vectors` extra when true. |
| `model` | `BAAI/bge-small-en-v1.5` | sentence-transformers model name. |

## `context`

| Key | Default | Meaning |
|---|---|---|
| `total_context_tokens` | `48000` | Budget for assembled context. Lowest-priority blocks shrink first. On the 32k Gemma write model this budget is capped at about 24000 tokens so the pack plus the completion fit. |
| `continuity_chars` | `6000` | Tail of the story-so-far string kept before the token cap. |
| `block_caps.instructions` | `2000` | Per-block token cap. |
| `block_caps.continuity` | `2000` | Per-block token cap. |
| `block_caps.chapter_frame` | `600` | Per-block token cap. |
| `block_caps.covered` | `800` | Per-block token cap. |
| `block_caps.canon` | `3000` | Per-block token cap. |
| `block_caps.continuity_state` | `1200` | Per-block token cap. |
| `block_caps.threads` | `800` | Per-block token cap. |
| `block_caps.characters` | `3000` | Per-block token cap. |
| `block_caps.world` | `1500` | Per-block token cap. |
| `block_caps.scene_sensory` | `300` | Per-block token cap. |
| `block_caps.summaries` | `2500` | Per-block token cap. |
| `block_caps.passages` | `2500` | Per-block token cap. |
| `block_caps.craft` | `1500` | Per-block token cap. |
| `block_caps.voices` | `1500` | Per-block token cap. |
| `scene_max_words_per_call` | `6000` | Splits a scene's beats across drafter calls. |
| `working_chunk_words` | `500` | Stored on `BookContext`. No other reader in this tree. |
| `prior_text` | `full` | `full` keeps recent chapter prose in the chapter pack (`pack.recent_chapters` full texts, older chapters as summaries). `summaries_plus_prev_plan` drops that prose. A call then gets summaries of every prior chapter, the previous chapter's plan (`plan.plan_file` when that file exists, otherwise the outline chapter card, scene cards, and beats), and the current chapter's plan by the same lookup plus `plan.notes_files`. The write call still gets the last `write.prior_words` (default 1500) of text already written in the current chapter. Beat planning, critique, and the write step all use this pack. A set `write.context_budget_tokens` trims after this selection. This mode also limits `characters.md` to the chapter cast. The Gemma example sets `summaries_plus_prev_plan`. |
| `characters` | `null` | `null` keeps the whole `characters.md` unless `prior_text` is `summaries_plus_prev_plan`. `chapter_cast` keeps only entries for this chapter: every scene card's `characters_present` and POV, anyone named in the chapter plan, notes, or beats (headings, aliases, and first names, case-insensitive), plus `always_characters`. Voice sheets stay limited to the scene's speakers. |
| `always_characters` | `[]` | Names that stay in `characters.md` when the cast filter is on, such as `Eve Fifth`. |

## `brainstorm`

| Key | Default | Meaning |
|---|---|---|
| `web_research` | `true` | Run xAI web search unless the project was created with `--no-research`. |

## `canon`

| Key | Default | Meaning |
|---|---|---|
| `conflict_policy` | `plan_wins` | `plan_wins` or `canon_wins`. `canon_wins` keeps the new detail. |
| `allow_canon_wins` | `true` | A judge may still keep a new detail when the policy is `plan_wins`. |
| `review_confidence` | `0.6` | Below this, the conflict goes to `bookforge review`. |
| `critical_auto_confidence` | `0.85` | Critical conflicts need at least this confidence to auto-resolve. |
| `reextract_after_revise` | `true` | The revise step re-extracts canon from the edited text. |

## `write`

| Key | Default | Meaning |
|---|---|---|
| `require_human_seed` | `true` | Before drafting, the first scene needs a non-whitespace human seed. Non-TTY runs raise and name the path. A TTY can paste one (end with a line that is only `.`, or EOF); it is saved to the chapter seed path. Set `false` to draft with no seed. |
| `raw_dir` | `null` | When set, the write step also writes `<raw_dir>/<chapter_id>-vN-<family>-raw.txt`. `N` is the next free version (`v1`, then `v2`). `family` is `gemma` when the write-role model id contains `gemma`, otherwise a short slug. Same flag as `bookforge run --raw-dir`. |
| `beat_by_beat` | `false` | One write call per beat group. The Hugging Face Gemma write tier turns this on even when the flag is false. Other tiers stay on `context.scene_max_words_per_call` until the flag is true. |
| `beats_per_call` | `1` | Beats in one call when beat-by-beat is on. `2` drafts a pair. Any other value is treated as `1`. |
| `context_budget_tokens` | `null` | Unset keeps today's write prompt. When set, the prompt is rebuilt in priority order and trimmed from the bottom until `prompt_token_estimate` fits: (1) this call's beats and the scene card, (2) voice sheets for `characters_present`, (3) continuity and facts for entities in the scene, (4) the last `prior_words` of text so far, (5) older chapters as summaries and a condensed style bible. If beats, the card, and the facts block still do not fit, one plan-role call condenses the facts block and that text is reused for the scene. Each LLM call logs `prompt_tokens_est` beside `prompt_tokens`. The Gemma example sets this to `24000`. |
| `prior_words` | `1500` | Words of prior prose kept when a budget is set: the current scene so far, otherwise the previous chapter tail. |

Seed path: `<project>/manuscript/<chapter_id>/human_seed.md`. It applies to the chapter's first scene. Optional per-scene override: `<project>/manuscript/<chapter_id>/sceneK/human_seed.md` (`scene1` and `scene01` both work). The override replaces the chapter seed for that scene.

Format: plain UTF-8 markdown. Optional YAML front matter is one opening block (`---` line, YAML, closing `---` line). Only that block and the newline that ends the closer are removed. The body then drops exactly one trailing end-of-line when the file has one (`\r\n` counts as one). No other character is dropped or normalized, including a trailing space on an earlier line. If the body's last non-space character is an unmatched opening quote (`"` or U+201C), the continuation is appended with nothing inserted, and the write prompt says the model is continuing inside that spoken line. Otherwise the continuation is joined with a blank line (`\n\n`). The seed is never passed through scrub, `clean_prose`, `enforce_first_line`, or protect. Only the continuation is cleaned.

After each beat-by-beat call the write step runs `canon_pipeline.process_scene` with that call's `chunk` index, then a plan-role judge (JSON) asking whether the chunk does its beats and only them. A non-verdict plan reply is ignored. Drift or a missed beat is retried once with the reason in the prompt. A second failure is recorded in the step folder's `drift_report.json` and the write continues. Summarize, the scene-level canon pass, and craft still run after the beat loop.

`protect.py` does not mark those paragraphs. Exact-line protection is off unless the chapter or `BOOKFORGE_PROTECT_EXACT_LINES` turns it on, and `norm()` folds punctuation, so a verbatim seed cannot be registered there without also protecting unrelated `KEEP` lines. The write step puts the opening back if craft scrub or `enforce_first_line` rewrites it. Later edit and revise passes do not have a seed lock. That is a known limit.

## `revise`

| Key | Default | Meaning |
|---|---|---|
| `recritique` | `true` | Present in `DEFAULT_CONFIG`. The revise step does not read it. |
| `max_passes` | `1` | Present in `DEFAULT_CONFIG`. The revise step does not read it. The editor runs once. |

## `deai`

| Key | Default | Meaning |
|---|---|---|
| `max_flag_ratio` | `0.0` | Present in `DEFAULT_CONFIG`. No reader outside the defaults test. |
| `max_loops` | `2` | Loops in the residual rewriter. |
| `min_severity` | `2` | Detector hits sent onward, and the screen's de-AI candidates. |
| `cluster` | `3` | This many milder hits in one paragraph also request a rewrite. |
| `fail_on_tokens` | `true` | The de-AI step raises if token tells remain after the last scrub. |
| `fragment_max_ratio` | `0.02` | Narration fragment share that flags `fragment_budget`. |
| `dash_policy` | `fiction` | Documents the pipeline's dash policy. `deai.scrub` always calls fiction mode and does not read this key. |
| `prompt_rules` | `true` | Include the fixed `PROSE RULES` block. False sends an empty block. |
| `route_to_editor` | `true` | False makes `located_hits` return nothing, so the editor is not handed detector findings. |
| `max_findings` | `40` | Cap on located findings. |
| `edit_guard` | `section` | In a packet, revert a section whose detector score falls by more than `max_tell_drop`. On a full-chapter edit, warn. `off` disables both. |
| `max_tell_drop` | `0.5` | Allowed drop in the 0–10 detector score before the guard acts. |
| `rewrite_residual` | `false` | When true, the de-AI step rewrites paragraphs that still have a severity >= 2 narration hit, then scrubs again. |
| `external_detector.provider` | `none` | `none` records `skipped`. `pangram` is report-only and does not edit. |
| `external_detector.api_key_env` | `PANGRAM_API_KEY` | Variable name checked when the provider is `pangram`. The key is not logged. |
| `external_detector.cap_usd` | `1.0` | Reserved for a future Pangram call. The HTTP client is not implemented. |
| `external_detector.mode` | `report` | The hook never changes the chapter. |

## `screen`

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `true` | `revise` screens before the editor. False sends the whole chapter. |
| `jev` | `true` | False skips Jev and keeps code checks (`skipped: disabled`). |
| `deai_questions` | `true` | Ask the six de-AI questions on the per-section Jev call. |
| `model` | `jev-1.13.0` | Pinned Jev model sent on every screen call. |
| `section_words` | `600` | Target words per section after scene breaks. |
| `cap_usd` | `1.0` | Stop further Jev calls when `state/jev_calls.jsonl` spend reaches this. |
| `skip_edit_if_clean` | `true` | Skip the editor when nothing is flagged and there are no chapter-level findings. |
| `fallback_full_edit` | `true` | On a bad packet reply, edit the whole chapter. |
| `packet_system_files` | `["style_bible.md"]` | Style files added to the packet's system prompt. |
| `facts_dir` | empty | Empty uses `<bible>/book<N>/` when that directory exists, else `state/facts/`. |
| `timetable_file` | empty | Empty loads no timetable. A relative path is under the bible dir, or the project dir if there is no bible dir. |
| `subjects` | `[]` | Subject names for the session-subject check. |
| `session_subjects` | `{}` | Map of session name to allowed subjects. |
| `afternoon_only` | `[]` | Subjects that must not appear in a sentence that says "morning". |
| `event_times` | `{}` | Event keyword to allowed `H:MM` list. Overrides the parsed timetable. |
| `max_new_per_scene` | `0` | `0` does not flag new names. A positive integer flags a scene that introduces more. |
| `default_rules` | `[]` | Copied into the generated facts file. |
| `canon` | `[]` | Extra canon lines copied into the facts bible block. |

## `plan`

Off unless you set a template and the file exists. Paths are relative to the project. `{chapter_id}` is replaced (for example `ch01`).

| Key | Default | Meaning |
|---|---|---|
| `plan_file` | `null` | One path template, such as `book1/{chapter_id}-plan.txt`. When the file exists, its text is included in beat planning (`_plan_beats`). |
| `notes_files` | `[]` | Path templates, such as `notes/{chapter_id}-beat_notes.md`. When those files exist, their text is included too, with the instruction that notes override the plan file. |

Changing either file changes the scene beat-cache key (a hash of the file bytes), so cached beats are planned again.

## `plan_sync`

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `true` | After a heavy revision, resync the stored scene plan (`threads resync` and the revise path). |

## `voice`

`voice.revoice` is not called from `revise` or any other step. The keys are loaded by `voice.cfg` when something calls that module.

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `false` | The re-voice function returns the text unchanged when this is false. |
| `role` | `revision` | Role for a re-voice call. |
| `min_ratio` | `0.0` | Minimum output length relative to the input. |
| `max_ratio` | `1.0` | Maximum output length relative to the input. |
| `max_tell_drop` | `0.3` | Reject a re-voice whose detector score falls by more than this. |
| `instructions` | empty | Extra text appended to the re-voice prompt. |

## `style`

| Key | Default | Meaning |
|---|---|---|
| `bible_dir` | `null` | Directory for `style_bible.md` and `voices/` when they are not in the project. `metadata.style_bible_dir` overrides per project. |
| `prefix_tokens` | `2500` | Style-bible budget in the cached writer prefix. |
| `planner_tokens` | `2000` | Style-bible budget for planner prompts. |
| `planner_voice_tokens` | `1500` | Voice-sheet budget for planner prompts. |

## `craft`

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `true` | Outline builds a craft plan. False skips planning and rolling refine. |
| `framework` | `save_the_cat` | `save_the_cat`, `three_act`, `seven_point`, `story_grid`, or `hero_journey`. |
| `scene_sequel` | `true` | Swain scene and sequel shapes on scene cards. |
| `max_key_items_per_scene` | `3` | Cap on load-bearing craft items scheduled into one scene. |
| `min_setup_gap_chapters` | `2` | Important plants stay at least this many chapters before payoff. |
| `rolling_refine` | `true` | After a chapter, refine the next chapter's cards. |
| `rerank` | `false` | Second ending plus a judge call on high-tension scenes. |
| `rerank_min_tension` | `7` | Tension at or above this, on a 10 scale, before rerank runs. |

## Ready-to-copy user block

`bookforge config-init` writes this shape (full file, key-free). Library defaults are unchanged until the file is present. `talkie` stays in the file with `enabled: false`: an opt-in fallback reword, not the default writer. The drafter tier in code is still `openai/gpt-6-sol`.

```yaml
llm:
  providers:
    huggingface:
      kind: openai
      base_url: https://router.huggingface.co/v1
      api_key_env: HF_TOKEN
  models:
    gemma:
      provider: huggingface
      model: nbeerbower/Gemma4-Gutenberg-31B-Heretic:featherless-ai
      max_tokens_multiplier: 1.0
      max_tokens_headroom: 1024
      context_tokens: 32768
  roles:
    write: {tier: gemma}
    rerank: {tier: gemma}
    # plan, outline, extract, deconflict, state_update, summary, talkie_fix,
    # audit, critique, edit, and the other planner/fact/fix/flow roles:
    plan: {provider: openrouter, model: deepseek/deepseek-v4.1-flash-1017}
    outline: {provider: openrouter, model: deepseek/deepseek-v4.1-flash-1017}
    extract: {provider: openrouter, model: deepseek/deepseek-v4.1-flash-1017}
    deconflict: {provider: openrouter, model: deepseek/deepseek-v4.1-flash-1017}
    state_update: {provider: openrouter, model: deepseek/deepseek-v4.1-flash-1017}
    summary: {provider: openrouter, model: deepseek/deepseek-v4.1-flash-1017}
    talkie_fix: {provider: openrouter, model: deepseek/deepseek-v4.1-flash-1017}
    audit: {provider: openrouter, model: deepseek/deepseek-v4.1-flash-1017}
    critique: {provider: openrouter, model: deepseek/deepseek-v4.1-flash-1017}
    edit: {provider: openrouter, model: deepseek/deepseek-v4.1-flash-1017}
  prices:
    nbeerbower/Gemma4-Gutenberg-31B-Heretic:featherless-ai:
      input: 0.48
      cache_read: 0.10
      output: 1.44
write:
  context_budget_tokens: 24000
  beat_by_beat: true
  beats_per_call: 1
context:
  prior_text: summaries_plus_prev_plan
  always_characters:
    - Eve Fifth
talkie:
  enabled: false
```

Gemma context is 32k tokens. USD per 1M: input 0.48, cached (cache read) 0.10, output 1.44. No reasoning body is sent to that model. The example write block above is the Gemma drafter: a 24000-token context budget, one beat per call, and talkie left off as an opt-in fallback. `context.always_characters` keeps that name in `characters.md` when the chapter-cast filter is on. Library defaults do not set that budget, do not turn `beat_by_beat` on, leave `prior_text` at `full`, and leave `always_characters` empty.

## `talkie`

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `false` | Opt-in fallback reword (base-model paraphrase plus a budgeted fact fix). Not the default writer. The pipeline makes no talkie calls until this is true. `bookforge talkie run` is a separate explicit opt-in. |

## Keys the code applies that are not in `DEFAULT_CONFIG`

These are easy to document as if they were config defaults. They are not in the template `config-init` writes. Code fills them in when the key is absent.

| Where | Key | Filled-in value | Meaning |
|---|---|---|---|
| `screen.cfg` in `core/screen.py` | `workers` | `4` | Thread pool size for per-section Jev calls. |
| `screen.cfg` | `context_paras` | `1` | Paragraphs of neighbouring context kept with a flagged section. |
| `pack.cfg` in `core/pack.py` | `enabled` | `true` | Story pack on or off. There is no `pack` block in `DEFAULT_CONFIG`. |
| `pack.cfg` | `files` | continuity, style bible, characters, plot, decisions log | Bible filenames loaded from the bible directory. |
| `pack.cfg` | `beat_sheet` | empty | Empty picks the newest `book*-beats*.md` in the bible directory. |
| `pack.cfg` | `world_file` | `world.md` | World file in the bible directory. |
| `pack.cfg` | `world_tokens` | `4000` | Token budget for the relevant world sections. |
| `pack.cfg` | `recent_chapters` | `2` | Full text of this many recent chapters in the pack. |
| `pack.cfg` | `voice_tokens` | `6000` | Token budget for the speakers' voice sheets. |
| `craft.cfg` in `craft/base.py` | `beat_tolerance_pct` | `5` | Framework beats realized within this many points of their target percent. |
| `craft.cfg` | `tension_min_correlation` | `0.3` | Audit threshold for planned versus realized tension. |
| `craft.cfg` | `law1_min_gap_chapters` | `1` | Magic rules shown at least this many chapters before they solve a conflict. |
| `craft.cfg` | `no_new_magic_after_pct` | `60` | No new magic after this percent of the book. |
| `craft.cfg` | `max_new_concepts_per_scene` | `3` | New-concept cap per scene. |
| `craft.cfg` | `subversions_per_act` | `1` | Expected trope subversions per act. |
| `craft.cfg` | `modules` | `null` | Null runs every registered module. A list limits the plan. |

Older docs named `screen.facts_dir: plans`, `screen.timetable_file: bible/schedule.txt`, and `screen.max_new_per_scene: 2`. Those keys exist. Those values are not the defaults.

`CRAFT_DESIGN.md` is named from the craft CLI help and is not in this repository.
