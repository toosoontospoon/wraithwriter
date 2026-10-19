"""`bookforge craft ...` commands."""
import json
from pathlib import Path

import click

from bookforge.core.context import BookContext


def _ctx(project):
    return BookContext(Path(project))


def _print_issues(issues):
    if not issues:
        click.echo("  no issues")
    for i in issues:
        loc = f"{i.get('chapter') or ''}{(':' + str(i['scene'])) if i.get('scene') else ''}"
        click.echo(f"  [{i['severity']}] {i.get('module', '')}/{i['check']} {loc} {i['description']}"
                   + (f"\n      fix: {i['fix']}" if i.get("fix") else ""))


@click.group("craft")
def craft_group():
    """Craft layer: plans, ledgers and checks (see CRAFT_DESIGN.md)."""


@craft_group.command("plan")
@click.option("--project", required=True, type=click.Path(exists=True))
@click.option("--framework", default=None, help="Override the framework for this project")
@click.option("--force", is_flag=True, help="Re-ask every planner instead of using cached plans")
def craft_plan(project, framework, force):
    """(Re)build the craft plan for an outlined project (also migrates older projects)."""
    from bookforge.craft import engine
    ctx = _ctx(project)
    if framework:
        ctx.metadata["craft_framework"] = framework
        ctx.save_json("metadata.json", ctx.metadata)
    issues = engine.plan_all(ctx, ctx.new_step_folder("craft_plan"), force=force)
    click.echo(f"Craft plan built ({engine.ledger(ctx).data['framework']}).")
    _print_issues(issues)


@craft_group.command("check")
@click.option("--project", required=True, type=click.Path(exists=True))
@click.option("--phase", type=click.Choice(["plan", "audit", "both"]), default="both")
def craft_check(project, phase):
    """Show plan-time validator results and/or the realized (audit) craft checks."""
    from bookforge.craft import engine
    from bookforge.craft.ledger import PlanContext
    ctx = _ctx(project)
    L = engine.ledger(ctx)
    if not L.has_plan:
        raise click.ClickException("No craft plan. Run `bookforge craft plan` first.")
    if phase in ("plan", "both"):
        click.echo("PLAN CHECKS:")
        _print_issues(L.data["plan_issues"] or engine.validate_plan(L, PlanContext.from_ctx(ctx, L.data["framework"])))
    if phase in ("audit", "both"):
        click.echo("REALIZED (AUDIT) CHECKS:")
        _print_issues(engine.audit(ctx))


@craft_group.command("show")
@click.option("--project", required=True, type=click.Path(exists=True))
@click.option("--ledger", "which", default=None, help="Module name, 'cards' or 'events'; omit for a summary")
def craft_show(project, which):
    """Print a ledger: structure, promises, arcs, foreshadow, mystery, subplots, magic, ..., cards, events."""
    from bookforge.craft import engine
    from bookforge.craft.views import ledger_rows
    L = engine.ledger(_ctx(project))
    if not L.has_plan:
        raise click.ClickException("No craft plan. Run `bookforge craft plan` first.")
    if not which:
        click.echo(f"framework: {L.data['framework']}  version: {L.data['version']}")
        for name, data in L.data["modules"].items():
            n = sum(len(v) for v in data.values() if isinstance(v, list))
            click.echo(f"  {name}: {n} item(s)")
        click.echo(f"  planned events: {len(L.data['planned'])}  realized: {len(L.data['realized'])}  "
                   f"scene cards: {len(L.data['scene_cards'])}")
        return
    for row in ledger_rows(L, which):
        click.echo("  " + json.dumps(row, ensure_ascii=False))


@craft_group.command("timeline")
@click.option("--project", required=True, type=click.Path(exists=True))
@click.option("--tension", is_flag=True, help="Show planned vs realized tension per scene instead")
def craft_timeline(project, tension):
    """Chapter grid of clues, reveals, arc stages, threads... (P planned, R realized, X both)."""
    from bookforge.craft import engine
    from bookforge.craft.views import tension_series, timeline
    ctx = _ctx(project)
    L = engine.ledger(ctx)
    if not L.has_plan:
        raise click.ClickException("No craft plan. Run `bookforge craft plan` first.")
    ids = [c["id"] for c in ctx.load_outline()]
    if tension:
        keys = [f"{c['id']}:{s.get('scene_number')}" for c in ctx.load_outline() for s in c.get("scenes") or []]
        for r in tension_series(L, keys):
            p, x = r["planned"], r["realized"]
            click.echo(f"{r['scene']:>8} plan {'#' * (p or 0):<10} {p}  real {'#' * int(x or 0):<10} {x if x is not None else '-'}")
        return
    w = max([len(r["item"]) for r in timeline(L, ids)] + [10])
    click.echo(" " * w + " " + " ".join(c[-2:] for c in ids))
    for r in timeline(L, ids):
        click.echo(f"{r['item']:<{w}} " + " ".join(f"{r[c] or '.':>2}" for c in ids))
