"""Structural frameworks as beat lists with target positions (percent of book)."""

FRAMEWORKS = {
    # Blake Snyder / Jessica Brody, Save the Cat Writes a Novel
    "save_the_cat": [("opening_image", 0), ("theme_stated", 5), ("catalyst", 10), ("debate", 15),
                     ("break_into_two", 20), ("b_story", 22), ("fun_and_games", 35), ("midpoint", 50),
                     ("bad_guys_close_in", 62), ("all_is_lost", 75), ("dark_night_of_the_soul", 78),
                     ("break_into_three", 80), ("finale", 90), ("final_image", 100)],
    "three_act": [("setup", 0), ("inciting_incident", 12), ("plot_point_1", 25), ("midpoint", 50),
                  ("plot_point_2", 75), ("climax", 90), ("resolution", 100)],
    # Dan Wells
    "seven_point": [("hook", 0), ("plot_turn_1", 20), ("pinch_1", 37), ("midpoint", 50), ("pinch_2", 62),
                    ("plot_turn_2", 80), ("resolution", 100)],
    # Shawn Coyne, applied at book level
    "story_grid": [("inciting_incident", 10), ("progressive_complications", 40), ("turning_point", 70),
                   ("crisis", 80), ("climax", 90), ("resolution", 100)],
    "hero_journey": [("ordinary_world", 0), ("call_to_adventure", 10), ("refusal", 15), ("meeting_the_mentor", 20),
                     ("crossing_the_threshold", 25), ("tests_allies_enemies", 40), ("ordeal", 55),
                     ("reward", 65), ("road_back", 75), ("resurrection", 90), ("return_with_elixir", 100)],
}
# Arc stages pinned to book position (Weiland-style positive arc).
ARC_STAGES = [("lie_established", 0), ("first_doubt", 20), ("midpoint_glimpse", 50), ("relapse", 62),
              ("dark_night", 75), ("truth_embraced", 85), ("tested_choice", 92)]
NEGATIVE_ARC_STAGES = [("lie_established", 0), ("first_doubt", 20), ("midpoint_glimpse", 50),
                       ("relapse", 62), ("dark_night", 75), ("lie_embraced", 85), ("tested_choice", 92)]


def beats(name):
    if name not in FRAMEWORKS:
        raise ValueError(f"Unknown framework '{name}'. Choose: {', '.join(FRAMEWORKS)}")
    return FRAMEWORKS[name]


def tension_target(x: float) -> float:
    """Default sawtooth-rising curve over book position x in [0,1]; climax near 0.9."""
    if x >= 0.97:
        return 4.0
    if 0.85 <= x < 0.97:
        return 9.5
    base = 3 + 5.5 * x
    for dip in (0.5, 0.75):  # partial release after midpoint and all-is-lost
        if dip < x < dip + 0.05:
            base -= 1.5
    return base
