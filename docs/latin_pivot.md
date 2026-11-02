# Latin pivot

Opt-in drafting path. The write role drafts a scene in classical-style Latin, a local Talkie model translates each paragraph, and code plus Jev checks fidelity, tags AI tells, and may swap a word for a synonym. Fiction-mode scrub and the existing detectors then run with no further model call.

The mode is off by default. `draft.mode` is `standard`, and a normal `bookforge run --step write` does not enter this pipeline.

Turn it on in either of two ways:

- Set `draft.mode` to `latin_pivot`.
- Pass `--latin-pivot` on `bookforge run --step write`.

A standalone group, `bookforge latin-pivot`, runs the same stages for one chapter or scene without the rest of the write step.

## Stages

Order is fixed: `latin`, `translate`, `fidelity`, `tag`, `synonyms`, `pick`, `scrub`. `--from-stage` and `--to-stage` select an inclusive slice.

1. **latin.** The existing write role and the scene/beat prompt (beat sheet, human seed, canon) draft plain classical-style Latin, one paragraph per blank-line block, with proper names unchanged. Human-seed lines stay in English inside `<<SEED>>...<</SEED>>` and must be copied, not translated. When `motif_ban.enabled` is true, the Latin-draft prompt and the beat-planning prompt on this path both tell the model not to use or describe silence or quiet as a motif, and they list the banned English phrases and Latin stems. The standard-mode prompts are unchanged. Output: `latin.txt`. `latin_pivot.latin_file` or `--latin-file` skips the call and uses that file. A supplied file is checked, not regenerated. By default (`latin_draft.per_beat: true`) the draft is one write-role call per beat, at `latin_draft.temperature` 0.6 with `latin_draft.frequency_penalty` 0.15 (sent as a request field on OpenAI-compatible providers). Why: in trials, drafting a whole scene in one call at 0.7 with a 0.3 penalty broke down into fused, garbled Latin, and 0.6 with no penalty looped on repeated phrases. `per_beat: false` falls back to the write step's beat grouping.
2. **translate.** Each paragraph is one llama.cpp `/completion` through `bookforge.core.talkie.complete`. The prompt is a fixed header plus four example pairs (package data `fewshot.json`, or `latin_pivot.fewshot_path`), then the target paragraph and a trailing `English:`. `cache_prompt` is true. Sampling is temperature 0, top_k 1, repeat_penalty 1.05. `n_predict` is `clamp(2.2 × Latin words + 40, 80, 600)`. Stops are a blank line, `Latin:`, and a newline plus `Latin:`. A paragraph that is only a seed span is copied and is not sent. Output: `english_raw.txt` (the first translation). A later fidelity retry does not overwrite that file; the kept text is in the fidelity checkpoint.
3. **fidelity.** A code heuristic checks the English/Latin word ratio, quoted-speech counts, non-sentence-initial capitalised names, digits, and sentence count (`± max(1, 30%)`). When that passes and `fidelity.jev` is true, one Jev `noul` asks whether the English keeps every event and statement of the Latin, in order, with nothing added or dropped. A score below `fidelity.jev_threshold` retries the paragraph up to `fidelity.retries` times at temperature 0.3 with a new seed. A still-failing paragraph is kept and logged `flagged: true`. If Jev is unavailable the stage stays heuristic-only and records `jev_mode: unavailable`. Output: `fidelity.jsonl`.
4. **tag.** Before any tell-list scoring, `always_flag` matches are recorded and removed from the candidate list. They are not sent to Jev, and they never reach synonym or pick, so they are not auto-swapped. Output of that pass: `flag_for_edit.json` (paragraph index, sentence index, sentence, motif, matched text, and character offsets in the paragraph). The word `still` is flagged only in the stillness sense. An ambiguous `still` may take one Jev `noul` when `always_flag.still_classifier` is `jev` and Jev is available. If Jev is unavailable, the hit is kept and marked `ambiguous: true`. Candidates that remain come from the tell lists the de-AI code already loads (`ai_tells.yaml`, `banned_words.txt`, `antislop_phrases.txt`, and the skill banlist). Matches are case-insensitive on word boundaries, outside seed spans, and outside dialogue only when `tag.skip_dialogue` is true. One Jev request per paragraph scores each remaining occurrence. A hit is kept when its probability is at least `tag.threshold`, up to `tag.max_per_paragraph`. Output: `tags.jsonl`.
5. **synonyms.** No model. A `SynonymSource` supplies lemmas. The WordNet source imports `nltk` lazily and raises a clear error if `nltk` or the WordNet corpus is missing. It never downloads. Inflection covers `-s`/`-es`, `-ed`, `-ing`, `-ly`, `-er`/`-est`, and capitalisation. Tell-list hits, phrases longer than three words, and duplicates are dropped. Cap: `synonyms.max`. Install the optional extra with `pip install -e ".[latin_pivot]"` (alias `.[latin]`, `nltk` only), then fetch the corpus once: `python -m nltk.downloader wordnet omw-1.4`. nltk refuses a data folder that other accounts can write to; `-d .venv/nltk_data` keeps it inside the venv. Tests inject a dict source and do not need WordNet.
6. **pick.** For each kept occurrence that has a synonym, one Jev `choice` asks which option reads most natural in that sentence. The swap happens only when the choice is not the original and its probability beats the original by `pick.margin`. Output: `swaps.jsonl` (before, after, sentence, probabilities, reason).
7. **scrub.** The same fiction-mode scrub and catalog autofix as `--step deai` and `bookforge detect --fix`, then the existing detectors as a report only. Seed spans are masked so scrub and swaps cannot change those bytes. Markers are stripped in `final.txt`; the seed bytes stay. `assert_seeds_preserved` runs on that text. Reports: `detect.json` and `detect.txt`.

Seed markers that the translator mangles between surviving `<<SEED>>` tags are spliced back. If the markers themselves are lost, the paragraph is translated once more. A second loss fails that paragraph.

The write step still runs craft scrub on the returned scene. A latin-pivot seed often sits mid-paragraph, so the leading-seed guard does not cover it. Those seed lines are shielded before that scrub, restored afterward, and checked with `assert_seeds_preserved` on the scene draft. The chapter draft is the joined scenes, so the same bytes are in both files.

## CLI

```
bookforge run --project DIR --step write --latin-pivot [--chapter chNN]
bookforge latin-pivot run --project DIR --chapter chNN [--scene K]
    [--latin-file PATH] [--from-stage S] [--to-stage S] [--out DIR]
    [--no-jev] [--dry-run] [--cap-usd N]
bookforge latin-pivot estimate --project DIR --chapter chNN [same flags except --dry-run]
```

`--dry-run` and `estimate` print the stage list, the paragraph count, and a cost estimate. They make no Gemma, Talkie, Jev, or subprocess calls, and `--dry-run` does not create the run directory. When `latin.txt`, `english_raw.txt`, or `final.txt` already exist, the dry run also prints motif density for those files. Missing files count as zero.

Default output directory: `<project>/runs/latin_pivot/<chapter>/`, or `<chapter>-sK` when `--scene` is set. `latin_pivot.out_dir` or `--out` overrides it.

## Checkpoints

Each per-paragraph stage appends `checkpoints/<stage>.jsonl`. A row is keyed by paragraph index and a SHA-256 of that paragraph's input. A rerun skips a row whose hash still matches and rebuilds the stage file from the checkpoint. A crash loses at most the paragraph in flight. When `translate` is skipped, the fidelity checkpoint supplies the kept English if it has one row per paragraph; otherwise `english_raw.txt` is used.

## Talkie lifecycle

A context manager wraps translate and fidelity.

- On enter it pings `latin_pivot.talkie.endpoint` (default `http://127.0.0.1:8080`).
- If the server is down and `talkie.manage` is true, it runs `talkie.start_cmd` and waits up to `talkie.start_timeout_s` (default 60) for health. `start_cmd` defaults to an empty string. No server path is built in. Set the command in config when you want the run to start a server.
- On exit it runs `talkie.stop_cmd` (default `docker stop talkie`) when this run started the server, or when `talkie.stop_after` is true. `stop_after` left null follows `manage` (true only when manage is true). The stop runs on success, on an exception, on KeyboardInterrupt, and on a budget abort.

`talkie.manage` defaults to true, so a run ends by running `talkie.stop_cmd` (`docker stop talkie`) even when the server was already up. That stops a container another job is using; set `talkie.manage: false` (or `stop_after: false`) when sharing the server. With an empty `start_cmd`, a down server is still an error. Subprocess execution is injectable. Tests pass a fake runner and never call docker.

## Costs and the cap

`latin_pivot.cap_usd` defaults to `0.50`. `--cap-usd` overrides it for one run.

Before every billable call the run estimates the cost (prompt tokens about `chars / 4`) and raises `BudgetExceeded` before the call if spent plus that estimate would pass the cap. The same cap is passed into `jev.ask`. Talkie is $0. Gemma spend uses the logged delta when the provider reports one, otherwise the config price for the write role. Jev spend uses `jev.spent` or input tokens times $0.042 per million. Jev output tokens are free.

`run.json` records stage timings, call counts, cost by stage, flagged paragraph indexes, the cap, and `aborted_reason` when the cap stops the run. It also records `latin_motif_hits`, `always_flag_count`, and `motif_density` (hits per 1,000 words for the Latin, for `english_raw.txt`, and for `final.txt`). The `latin-pivot run` summary prints those three rates.

## Motif checks

Silence and quiet are strong AI-prose tells in this pipeline's outputs. Two checks keep them out of the automatic swap path.

Right after the Latin stage, `latin_hits` scans the draft with the stem patterns in `motif_ban.latin_patterns`. Matching is case-insensitive and skips `<<SEED>>` spans. `action: regenerate` rewrites only the chunk that hit, up to `max_regens`, and names the hits in the corrective note. Each regeneration is estimated against `cap_usd` and is not sent if it would pass the cap. Hits that remain, and every hit when `action` is `flag`, are kept in the text and written to `latin_motif_hits.json` (paragraph index, word, sentence).

At the start of tagging, the English `always_flag` list is matched with word boundaries. Single words allow a short inflection (`-s`, `-ed`, `-ing`, `-ly`, `-er`, `-est`). Multi-word phrases allow flexible whitespace. Seed spans are skipped. Those hits are always flagged. They are not scored and not swapped. `motif_density` is hits per 1,000 words: the always-flag list on the English, and `latin_hits` on the Latin. Adverb `still` ("yet") does not count.

Rough sizes, not a quote:

- Talkie: about 13 minutes per 1,000 Latin words on an 8-thread CPU at Q4.
- Jev: $0.042 per million input tokens (`PRICE_PER_MTOK` in `bookforge/core/jev.py`).
- Gemma: `llm.prices` for the write role. The config-init Gemma example is USD per 1M tokens: input 0.48, cached read 0.10, output 1.44.

The offline estimate includes Talkie minutes and a lower bound on fidelity Jev calls. Tag and pick costs depend on how many tell-list hits survive, so they are omitted from that estimate. A Latin draft with no source file yet is reported as an unknown Gemma cost.

## Config keys

See [config.md](config.md). Jev questions use `screen.model` (default `jev-1.13.0`).
