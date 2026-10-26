# Jev tell tagger + guarded reviser: SHELVED

**Status: Shelved 2026-10-26 by Ben's decision; not merged.** Code stays on branch `jev-tells` (PR #2, closed).

## What was built
- A Jev tagger: one Jev call per page asks a yes/no question for each sentence across 12 tell categories (ai_vocab, stock_phrase, filter_hedge, rule_of_three, tidy_ending, emotion_explained, sensory_listing, not_x_but_y, generic_detail, portentous_abstraction, mirrored_lines, gesture_beat), plus page-level scores.
- Code checks for repeated actions and uniform sentence rhythm.
- A guarded reviser (`jev_revise`, mistral-small-3.2): rewrites flagged sentences, with Jev checks for added facts and deletions, a cap on words cut, and undo-if-worse. Off by default.

## Calibration (in-sample, optimistic)
4 planted pages, thresholds tuned on the same pages: precision 0.80, recall 0.92, F1 0.85 (35 correct, 9 false alarms, 3 missed).

## A/B on 6 held-out texts
Arms: A = autofix + scrub (current default); A2 = A + severe-only `rewrite_flagged`; A2f = A + full `rewrite_flagged`; B = A + Jev tagger -> guided rewrite (allow_cuts=False, all guards on).

| text | B words vs A | tagger severity A/A2/A2f/B* | P(drops a fact) A/A2/A2f/B | P(emotion kept) A/A2/A2f/B | fakespot P(AI) A/A2/A2f/B |
|---|---|---|---|---|---|
| ch01 | -6.5% | 24/21/18/10 | .05/.45/.47/.90 | .94/.63/.74/.40 | .87/.87/.70/.43 |
| ch02 | -3.5% | 16/15/17/3 | .06/.77/.07/.90 | .90/.45/.90/.39 | .69/.64/.69/.62 |
| gen1 (B==A) | 0 | 5/5/5/5 | .03/.09/.08/.03 | .96/.89/.90/.95 | .72 all |
| gen2 | -0.2% | 1/1/1/0 | .02/.02/.02/.36 | .94/.93/.94/.75 | .93/.92/.92/.77 |
| gen3 | -3.5% | 49/43/42/35 | .07/.90/.84/.79 | .91/.17/.38/.41 | .97/1.0/1.0/1.0 |
| narration | -8.0% | 25/18/18/7 | .02/.41/.42/.61 | .97/.73/.70/.62 | .74/.71/.63/.21 |
| total | | 120/103/101/60 | | | |

\*Circular for B: the reviser optimizes against this same tagger.

Findings:
- **No lift shown.** B halves tagger severity, but that is circular.
- **Blind judge mostly split** (position-consistent wins only): A v B: B 2, A 0, split 3. A2 v B: B 1, split 5. A2f v B: A2f 2, B 1, split 3.
- **Fidelity worse.** A judge with the reference picked A over B on ch01, ch02, gen3 and narration; Jev P(drops a fact) for B hit 0.6-0.9 vs about 0.05 for A.
- **Fakespot is fragile.** The straight-quote scrub alone moved gen1 from .35 to .72 and ch02 from .33 to .69. The roberta detector called nearly everything human.

## Failure examples
- List items dropped: "same glass, same red wax seal, same cramped handwriting" lost an item.
- Contrasts deleted: the denied half of not-X-but-Y lines removed.
- Specifics blurred: "Three years." became "Time had passed."
- A rewrite left a broken (unbalanced) quote.

## Root causes
1. The `rule_of_three` and `not_x_but_y` fix text in the taxonomy tells the reviser to delete (cut the triple to one or two items; drop the denied half).
2. The fidelity guard only checks for added facts, never dropped ones.

## If ever revived, fix these first
1. `allow_cuts=False` by default (rewrite only, never cut).
2. Reword the `rule_of_three` and `not_x_but_y` fixes to keep every item.
3. Add a dropped-fact / emotional-beat check.
4. Add per-sentence length and quote-balance checks.

## Broader lesson
Pangram-class detectors key on token origin, so rewriting chat-model text, by tagger or otherwise, barely moves them. Current direction: draft or reword with a base model (tests with talkie-1930-13b-base).

## Spend
About $0.05 for the build run and $0.28 for the A/B (Jev + OpenRouter).
