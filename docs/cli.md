# Commands

Entry points: the `bookforge` script (`bookforge = bookforge.cli:main`) and `python -m bookforge.cli`. Both take a global `--verbose` flag.

Python 3.10 or newer. Install with `pip install -e ".[test]"`. See the README for extras and environment variables.

A run needs `--step`, both `--from` and `--to`, or `--restart-from`. Giving only one of `--from` or `--to` is not a range.

## `init`

```
bookforge init PROMPT [--words N] [--project DIR] [--no-research]
```

Creates a project directory and `metadata.json` (`prompt`, `created`, `no_research`). Refuses to overwrite a directory that already has `metadata.json`.

| Flag | Meaning |
|---|---|
| `PROMPT` | Premise. Required. |
| `--project` | Directory. Default: `projects/<slug>_<unix-ts>` (slug is the first 30 characters of the premise). |
| `--words` | Accepted and ignored. Length follows the story. |
| `--no-research` | Stored on the project. Brainstorm skips web research. |

## `run`

```
bookforge run --project DIR [--step NAME] [--from A --to B] [--restart-from NAME]
              [--chapter ch03] [--force] [--edit-effort low|medium|high]
```

`--project` must already exist.

| Flag | Meaning |
|---|---|
| `--step` | One step, or `all`. Names: `brainstorm`, `outline`, `initialize_cast_and_world`, `write`, `critique`, `revise`, `deai`, `audit`, `finalize`. |
| `--from` / `--to` | Inclusive range, in pipeline order. Both are required. An inverted range prints `Invalid range` and does nothing. |
| `--restart-from` | Marks that step and every later step stale. Chapter manuscripts at or before `write` are moved to `archive/<timestamp>/`, and their canon provenance, passages, and summaries are retracted. If `--step` is omitted, the command stops after invalidating. |
| `--chapter` | Chapter id, for example `ch03`. Without it, `write`, `critique`, `revise`, and `deai` run for every outline chapter that is missing or stale. |
| `--force` | On `--step all`, rerun steps that are already current. `finalize` is still called without `force`, so a missing or failed audit still stops it. On a chapter step with no `--chapter`, rerun chapters that are already current. On `--step finalize`, pass `force` into that step: unfinished chapters and a failed or missing audit are allowed. A single named step other than `finalize` always runs, so `--force` changes nothing there. `--force` is not applied to a `--from`/`--to` range. |
| `--edit-effort` | Sets `BOOKFORGE_EDITOR_REASONING_EFFORT` for this process (`low`, `medium`, or `high`). |

`all` runs each stale step through `finalize`: the three book steps, then `write` → `critique` → `revise` → `deai` per chapter, then `audit` and `finalize`. Up-to-date steps are skipped unless `--force` is set.

Aliases accepted by `--step`, `--from`, `--to`, and `--restart-from`:

| Alias | Step |
|---|---|
| `init`, `initialize` | `initialize_cast_and_world` |
| `draft` | `write` |
| `polish`, `global_polish` | `audit` |
| `de-ai`, `deai_chapter` | `deai` |

## `status`

```
bookforge status --project DIR
```

Prints book-step freshness, per-chapter marks for `write critique revise deai`, canon counts (facts, open threads, open revision tasks, pending reviews), and spend loaded from `costs.jsonl`.

## `cost` and `costs`

```
bookforge cost --project DIR [--by chapter|step|role|model] [--chapter ch03]
bookforge costs --project DIR
```

`cost` reads `<project>/state/usage.jsonl`. The default `--by` is `chapter`. Columns are calls, prompt tokens, cached tokens, cache hit rate, cache-write tokens, completion tokens, and cost.

`costs` reads `<project>/costs.jsonl` and totals calls and tokens by role. That file is also what `status` sums. Jev screen calls are logged as role `jev_screen` when the call succeeds.

There is no separate credit-check command. A missing API key raises `Missing API key: set environment variable <NAME>` before the call. HTTP 429 and 5xx are retried (`llm.http_retries`, default 4). Other HTTP errors, including an out-of-credit response, raise `LLMError` and stop that call. `llm.budget_usd` (default `null`, no cap) raises `BudgetExceeded` before the next call once recorded spend has reached the cap. The pipeline does not finish a partial scene for you.

## `config-init`

```
bookforge config-init
```

Writes `DEFAULT_CONFIG` as YAML to `$BOOKFORGE_CONFIG` if that variable is set, otherwise to `~/.bookforge/config.yaml`. The write happens only when the file is missing. The command prints `Wrote <path>` either way. Keys stay in the environment. A present file is deep-merged over the defaults at load time.

## `screen`

Does not call the generative editor.

```
bookforge screen facts --project DIR --chapter ch01
bookforge screen run   --project DIR --chapter ch01 [--text FILE] [--no-jev]
```

`facts` regenerates `<facts dir>/<chapter>-facts.json` and keeps top-level keys listed in `_manual`. With `screen.facts_dir` empty (the default), the directory is `<bible>/book<N>/` when `metadata.book` is set and that directory exists, and `state/facts/` otherwise.

`run` screens the draft (or `--text`) and writes `revision_packet.md`, `revision_packet.json`, and `screen_estimate.json` under a new `steps/` folder. `--no-jev` forces code checks only.

## `structure`

```
bookforge structure show    --project DIR
bookforge structure check   --project DIR [--run-limit 3] [--quiet]
bookforge structure percent --project DIR [--write]
```

`check` exits 1 when any chapter FAILs. `--run-limit` defaults to 3. `--quiet` hides INFO. `percent` uses word counts and the mean length for unwritten chapters. `--write` stores `structure.percent` on the outline. Field rules are in `STRUCTURE.md`.

## `threads`

The ledger file is `<project>/threads.yaml`.

```
bookforge threads show   --project DIR
bookforge threads plan   --project DIR --chapter ch01 [--dry-run] [--scenes | --no-scenes]
bookforge threads check  --project DIR --chapter ch01 [--file PATH] [--update | --no-update]
bookforge threads resync --project DIR --chapter ch01 [--file PATH] [--no-reextract]
```

`plan` asks the planner for obligations and, by default, scene briefs (`--scenes` is on). `--dry-run` does not save the outline.

`check` reads the chapter final unless `--file` is set. `--update` is on by default and writes results into the ledger flagged for review.

`resync` rebuilds the stored scene plan from the newest manuscript file, or from `--file`. It also re-extracts canon facts unless `--no-reextract` is set.

## `canon`

```
bookforge canon list    --project DIR [--entity TEXT] [--status TEXT] [--importance TEXT]
bookforge canon threads --project DIR
bookforge canon tasks   --project DIR
```

`list` prints ledger facts. `threads` prints the canon Chekhov ledger (setups and payoffs), which is separate from `threads.yaml`. `tasks` prints open revision tasks and plan amendments.

## `review`

```
bookforge review list    --project DIR
bookforge review resolve --project DIR ITEM_ID --decision plan|canon|ignore [--fix TEXT]
```

`--decision` is required. `plan` revises the text (`--fix` overrides the fix text). `canon` keeps the new detail and amends the plan. `ignore` accepts the item as is.

## `craft`

The outline step builds a craft plan when `craft.enabled` is true. These commands inspect or rebuild `state/craft/ledger.json`. The CLI help mentions `CRAFT_DESIGN.md`; that file is not in this repository.

```
bookforge craft plan     --project DIR [--framework NAME] [--force]
bookforge craft check    --project DIR [--phase plan|audit|both]
bookforge craft show     --project DIR [--ledger NAME]
bookforge craft timeline --project DIR [--tension]
```

`--framework` is stored on the project as `metadata.craft_framework` and overrides `craft.framework` for that run. Config values: `save_the_cat` (default), `three_act`, `seven_point`, `story_grid`, `hero_journey`. `--force` re-asks every planner instead of using cached plans.

`check` defaults `--phase` to `both`. `show` without `--ledger` prints a summary. A ledger name is a module (`structure`, `promises`, `arcs`, `foreshadow`, `mystery`, `subplots`, `magic`, and the others in the registry), `cards`, or `events`. `timeline` prints a chapter grid (`P` planned, `R` realized, `X` both). `--tension` prints planned versus realized tension per scene.

## `detect`

```
bookforge detect [PATH] [--style-dir DIR] [--json] [--fix] [--fail-on N]
```

Deterministic AI-tell detector. No LLM call. Omit `PATH` to read stdin. `--style-dir` is a directory with `style_bible.md` and `voices/` (allowlist and cast names). `--fix` prints the text after the safe autofix, which is the fiction-mode token scrub plus catalog autofix swaps. `--fail-on N` prints a family summary and exits 1 when any hit has severity at least N, any token tell remains, or `fragment_budget` is flagged.

## `migrate`

```
bookforge migrate --project DIR
```

Upgrades a v0.1 project: `ch01`… ids, renamed manuscript files, canonical state, and a canon ledger seeded from the plan. Later steps show as stale.

## Web UI

Not a `bookforge` subcommand. After `pip install -e ".[webui]"`:

```
streamlit run webui.py
```

It runs the same steps against `projects/` and the same `state/` and `manuscript/` files.
