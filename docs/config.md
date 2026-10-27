# Config reference

Source: `DEFAULT_CONFIG` in `bookforge/config.py`. A user file (`$BOOKFORGE_CONFIG`, or `~/.bookforge/config.yaml`) is deep-merged over this. `bookforge config-init` writes the defaults only when that file is missing.

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
| `total_context_tokens` | `48000` | Budget for assembled context. Lowest-priority blocks shrink first. |
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
