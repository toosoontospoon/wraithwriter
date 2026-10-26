"""Threshold scoring for the Jev tell calibrator. No network."""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "calibrate_jev_tells", ROOT / "tools" / "calibrate_jev_tells.py")
cal = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cal)


def test_pick_thresholds_prefers_f1_then_the_higher_tie():
    raw = {"pages": [{"items": [
        {"required": ["ai_vocab"], "acceptable": [],
         "probs": {"ai_vocab": 0.72, "stock_phrase": 0.95}},
        {"required": [], "acceptable": ["stock_phrase"],
         "probs": {"ai_vocab": 0.2, "stock_phrase": 0.95}},
    ]}]}
    result = cal.pick_thresholds(raw)
    # 0.72 stays a hit through 0.70 and becomes a miss at 0.75. Every tie picks the higher bar.
    assert result["thresholds"]["ai_vocab"] == 0.7
    assert result["categories"]["ai_vocab"]["tp"] == 1
    assert result["categories"]["ai_vocab"]["fp"] == 0
    assert result["categories"]["ai_vocab"]["f1"] == 1.0
    # The second stock_phrase is an acceptable extra, so it is not a false positive.
    assert result["thresholds"]["stock_phrase"] == 0.9
    assert result["categories"]["stock_phrase"]["fp"] == 1
    assert result["categories"]["stock_phrase"]["tp"] == 0
    assert result["overall"]["tp"] == 1 and result["overall"]["fp"] == 1


def test_write_thresholds_updates_a_copy_and_skips_code_checks(tmp_path):
    src = ROOT / "bookforge" / "jev_templates" / "tell_taxonomy.json"
    dest = tmp_path / "tell_taxonomy.json"
    dest.write_text(src.read_text())
    updated = cal.write_thresholds({"ai_vocab": 0.75, "uniform_rhythm": 0.9, "tidy_ending_page": 0.8}, dest)
    tax = json.loads(dest.read_text())
    by_id = {row["id"]: row for row in tax["categories"]}
    assert by_id["ai_vocab"]["threshold"] == 0.75
    assert {row["id"]: row["threshold"] for row in tax["code_categories"]}["uniform_rhythm"] == 0.5
    page = {row["id"]: row["threshold"] for row in tax["page_questions"]}
    assert page["tidy_ending_page"] == 0.8
    assert page["ai_vocab"] == 0.75
    assert "uniform_rhythm" not in updated
    # The planted pages shipped with the repo are the four calibration files, unchanged in shape.
    planted = list((ROOT / "tests" / "fixtures" / "jev_tells").glob("planted_*.json"))
    assert len(planted) == 4
    sample = json.loads((ROOT / "tests" / "fixtures" / "jev_tells" / "planted_bakery.json").read_text())
    assert sample["page_labels"] and sample["sentences"][0][1] == []
