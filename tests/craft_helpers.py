from bookforge.craft import REGISTRY
from bookforge.craft.base import Book, Events
from bookforge.craft.ledger import CraftLedger, PlanContext


def book(n=8, scenes=2):
    return [{"id": f"ch{i:02d}", "title": f"T{i}", "act": "", "summary": "",
             "scenes": [{"scene_number": s} for s in range(1, scenes + 1)]} for i in range(1, n + 1)]


def setup(tmp_path, n=8, scenes=2, framework="save_the_cat"):
    P = PlanContext(book=Book(book(n, scenes)), framework=framework,
                    chars={"finn": {"name": "Finn"}, "zara": {"name": "Zara"}, "elias": {"name": "Elias"}})
    L = CraftLedger(tmp_path / "state")
    return P, L


def plan(mod_name, raw, P, L):
    m = REGISTRY[mod_name]
    data = m.normalize(raw, P)
    notes = m.autofix_plan(data, P)
    if mod_name == "structure":
        L.data["scene_cards"] = data.pop("cards")
    L.data["modules"][mod_name] = data
    return m, data, notes


def ev(kind, ref, ch, scene=1, **data):
    e = {"kind": kind, "ref": ref, "ch": ch, "scene": scene}
    if data:
        e["data"] = data
    return e


def check(mod_name, events, P, L, mode="audit"):
    m = REGISTRY[mod_name]
    return m.check(L.module(mod_name), Events(events, P.book), L, P, mode)


def checks_named(issues, name):
    return [i for i in issues if i["check"] == name]
