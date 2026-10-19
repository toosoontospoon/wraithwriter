"""Craft layer: plan, brief, verify and audit storytelling techniques.

See CRAFT_DESIGN.md. Every technique is a ``CraftModule`` (registry in ``base``) that
contributes plan data, planned events, per-scene brief lines, per-scene observation
checks and whole-book checks. The same ``check`` runs on planned events (before
writing) and on realized events (audit), so plan and book are held to one standard.
"""
from bookforge.craft.base import REGISTRY, module_names  # noqa: F401
from bookforge.craft import modules_p0  # noqa: F401  (registers P0 modules)
from bookforge.craft import modules_p1  # noqa: F401  (mystery, subplots, magic)
from bookforge.craft import modules_p2  # noqa: F401  (relationships, world reveal, prophecy, mentor, tropes)
