# Story structure in WraithWriter

Every outline chapter can carry a `structure` block (`bookforge/core/structure.py`). The planner fills it in, the
writer and editor see it as a STRUCTURAL FUNCTION block, and `bookforge structure check` lints the whole book.

## The hard rule

No chapter may drag or feel unnecessary. Worldbuilding colour and whimsy are welcome, but every chapter needs:

- `plot_function`: one sentence on what changes in the A or B plot that would not happen without this chapter.
- `value_turn`: `{from, to}`, and the two must differ.
- A function of its own: one that doesn't duplicate the chapter before or after it.

`structure check` reports a breach of any of these three as **FAIL** and exits 1. Everything else is **WARN** or **INFO**.

## Fields

| field | values | meaning |
|---|---|---|
| `act` | 1, 2a, 2b, 3 | three acts, with Act 2 split at the midpoint |
| `percent` | 0-100 | chapter midpoint as % of the book by words (`structure percent` computes it) |
| `save_the_cat` | opening_image, theme_stated, setup, catalyst, debate, break_into_two, b_story, fun_and_games, midpoint, bad_guys_close_in, all_is_lost, dark_night_of_the_soul, break_into_three, finale, final_image (join two with `+`) | Snyder/Brody beat |
| `heros_journey` | ordinary_world, call_to_adventure, refusal_of_the_call, meeting_the_mentor, crossing_the_threshold, tests_allies_enemies, approach, ordeal, reward, road_back, resurrection, return_with_elixir | Vogler stage |
| `chapter_type` | action, reaction, discovery, set_piece, transition, bottle | the chapter's dominant mode |
| `strand` | A, B, AB | which plot it serves; `a_purpose` / `b_purpose` give one line each |
| `plot_function` | sentence | REQUIRED (see the hard rule) |
| `color` | text | optional: the flourishes it carries |
| `value_turn` | `{from, to}` or `"from -> to"` | REQUIRED, and it must change |
| `scene_sequel` | scene, sequel, scene+sequel, sequel+scene | Swain pattern |
| `try_fail` | text | e.g. "try 2/3 at sealing: fails (no, and)" |
| `setups` / `payoffs` | `thread:<id>`, `ledger:<n>`, free text | links to threads.yaml and the foreshadowing ledger |
| `ending` | cliffhanger, reveal, decision, resonant_image | how the chapter closes |

## Glossary

- **A plot / B plot**: A is the external story (the heist, the case, the war). B is the relationship or theme
  story that carries the meaning (two friends drifting apart, a found family forming).
- **Act 1 / 2a / 2b / 3**: setup (about 0-25%), the new world and its promise up to the midpoint (25-50%), pressure
  and collapse (50-80%), climax and resolution (80-100%).
- **Save the Cat beats**: Opening Image (a snapshot of before); Theme Stated; Setup; Catalyst (the life-changing
  event); Debate (whether to go); Break into Two (the choice into the new world); B Story (the relationship that
  teaches the theme); Fun and Games (the promise of the premise); Midpoint (a false victory or false defeat, stakes
  raised); Bad Guys Close In; All Is Lost (often a "whiff of death"); Dark Night of the Soul; Break into Three (the
  new idea that solves it); Finale; Final Image (a mirror of the opening).
- **Hero's Journey**: the mythic map from Ordinary World through Threshold, Tests, Ordeal and Reward to the Return
  with the Elixir.
- **Chapter types**: *action*, where the plot moves by deeds and conflict; *reaction*, a quiet sequel where a
  character feels the last blow and decides; *discovery*, where something is learned or revealed; *set_piece*, a
  showpiece of wonder or spectacle; *transition*, which moves us between places or times; *bottle*, one confined room
  or situation.
- **Value turn**: the charged value at stake (safe/unsafe, alone/together, trusted/suspected) flips across the
  chapter. A chapter with no turn is a non-event (McKee, Coyne).
- **Scene / sequel**: a scene is goal -> conflict -> disaster. A sequel is reaction -> dilemma -> decision (Swain).
- **Try/fail cycle**: repeated attempts that fail ("no, and" / "no, but") until one succeeds ("yes, but" / "yes").
- **Setup / payoff**: a plant and the place it fires (Chekhov's gun). An unpaid setup is a broken promise.
- **Ending types**: *cliffhanger* (cut mid-danger); *reveal* (new information turns things); *decision* (a choice
  that commits the next chapter); *resonant image* (a quiet picture that holds the feeling).

## What the checker flags

- FAIL: no `plot_function`; no `value_turn` or a turn that doesn't change; a `plot_function` that duplicates a
  neighbour (exact match, or word overlap >= 0.6).
- WARN: missing fields; unknown enum values; a strand with no purpose line; a Save the Cat beat more than 12 points
  from its target %; runs of the same `chapter_type` or `ending` (default 3+); acts outside their healthy share
  (1: 15-30%, 2a/2b: 18-32%, 3: 12-30%); acts out of order; setups with no later payoff; setups whose thread pays
  off in a chapter that doesn't list it.
- INFO: setups that aren't linked to a thread or ledger entry.

## CLI

    bookforge structure show    --project PROJECT     # table: act, %, beats, type, strand, ending, value turn
    bookforge structure check   --project PROJECT [--run-limit 3] [--quiet]   # exit 1 on any FAIL
    bookforge structure percent --project PROJECT [--write]   # positions by word count; --write stores them
