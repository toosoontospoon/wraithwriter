# Talkie reword

Status: experimental. A definitive detector test is pending.

The stage is off unless `talkie.enabled` is true. `bookforge talkie run` is an explicit opt-in even when the flag is false.

## What it does

After revise, and before de-AI, the step reads the revised chapter (or the draft when no revision exists) and writes `manuscript/<id>_talkie.txt`. It does not overwrite the draft or the revision. De-AI then prefers the talkie file when one exists.

Each prose paragraph is reworded in order. History is the paragraphs already fixed.

1. Build a context: a short synthetic few-shot, the cast line, a code-built story so far, the last `history_window` source paragraphs, and the last `history_window` fixed paragraphs.
2. The local base model writes `n_rerolls` candidates. Code checks names, length, quote balance, and a source 4-gram copy guard. One batched Jev call scores dropped facts, invented facts, contradiction, and names. The best passing candidate is kept, or the lowest summed Jev score when none pass.
3. Jev localises the paragraph: which source sentences were dropped, and which new sentences look wrong.
4. For each dropped source sentence the base model retells that sentence alone. The best retelling that keeps names, shares content words, and stays under the splice copy limit is inserted after the sentence it fits.
5. A content-word Jaccard check drops a spliced sentence that repeats one already kept.
6. Sol (planner tier, JSON) may edit numbered sentences or delete a flagged or duplicate sentence. It may not add a sentence. Code keeps Sol-written words at or under `max(3, edit_budget × paragraph words)`, skips later edits that would pass that budget, and restores quote marks on an edited sentence. If the base-model paragraph already passes, this call is skipped.
7. A style-drift check flags a large sentence-length shift or an AI-tell that showed up in the changed text.
8. A final Jev and code check is logged, and the paragraph is appended to the history. The log row is written immediately so a rerun can resume.

After the chapter, a targeted pass runs only on paragraphs whose final localise still shows a dropped fact. It does one more fact-splice round and a Sol fix with `targeted.edit_budget` (default 0.25). The new text is kept only when Jev's dropped score falls and no new invented, contradiction, or name failure appears. Both results are logged.

## Why

The point is base-model wording. Detectors such as Pangram read token origin, so a low score reflects who generated the tokens. It does not mean the prose got better.

## Setup

Download the weights yourself. This repo does not mirror them.

The base model is Apache-2.0: [talkie-1930-13b-base](https://huggingface.co/talkie-lm/talkie-1930-13b-base). A Q4_K_M GGUF is listed on [talkie-1930-13b-base-GGUF](https://huggingface.co/niklassheth/talkie-1930-13b-base-GGUF).

```bash
TALKIE_MODEL=/path/to/talkie-1930-13b-base-Q4_K_M.gguf tools/talkie_server.sh start
bookforge talkie ping
bookforge talkie run --project projects/myproject --chapter ch01
bookforge talkie run --file chapter.txt --out chapter_talkie.txt
tools/talkie_server.sh stop
```

`tools/talkie_server.sh` runs `ghcr.io/ggml-org/llama.cpp:server` in Docker with `--rm`, publishes `127.0.0.1:8080`, and starts the server with `-c 16384 -np 4`. `TALKIE_THREADS` (default 8) is passed to both `-t` and `-tb`. Set `TALKIE_WAIT_LOAD` to wait until the one-minute load average is under that number. The script never downloads or stores weights.

## Speed and cost

On a shared 8-core desktop CPU (Ryzen 7 3700X class), talkie time is about 40–65 minutes per 1,000 words. Jev plus Sol is about $0.12 per 1,000 words.

One measured chapter of 666 words cost $0.077 ($0.116 per 1,000 words) and used 43.4 minutes of talkie time (about 65 minutes per 1,000 words). Mean final Jev scores on that run: dropped 0.68, invented 0.38, contradiction 0.16, name 0.15.

## Example

Source: "Lila set the warm loaf on the stone sill and latched the bakery door."

Reword: "Lila put the warm loaf on the stone sill and shut the bakery door."

## A/B numbers

Jev pass means all four scores are at or under 0.5 and the code checks pass. Word shares are of the final paragraph. Sol 4-gram reuse is how much of Sol's original 4-grams remain.

| Arm | Jev pass | Facts kept | Sentences clean | Talkie words | Sol words | Sol 4-gram reuse | Style-drift flags |
|---|---|---|---|---|---|---|---|
| Sol original | 8/8 | 1.00 | 1.00 | 0% | 100% | 100% | – |
| Best-of-4 + Gemini fix | 1/8 | 0.25 | 0.43 | – | – | – | – |
| Best-of-4 + Sol style fix | 5/8 | 0.94 | 0.88 | 44% | 56% | 44% | 5/8 |
| Notes N0 (Sol, generic) | 8/8 | 1.00 | 1.00 | ~0% | ~100% | 49% | n/m |
| Notes N1 (Sol + talkie notes) | 8/8 | 0.98 | 0.98 | ~9% talkie 4-grams | ~91% | 72% | n/m |
| Rolling v3, Sol unconstrained (5 paragraphs) | 1/5 | 0.97 | 0.91 | 66% | 34% | 19% | 1/5 |
| All-words rolling | 0/8 | 0.83 | 0.96 | 83% | 17% | 21% | 1/8 |
