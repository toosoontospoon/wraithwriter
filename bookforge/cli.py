"""BookForge CLI.  `bookforge --help` (or `python -m bookforge.cli --help`)."""
import datetime
import json
import os
import time
from pathlib import Path

import click

from bookforge.config import write_template
from bookforge.core import progress
from bookforge.core.canon_pipeline import resolve_review
from bookforge.core.context import BookContext
from bookforge.core.schema import slugify


def _ctx(project, verbose=False):
    return BookContext(Path(project), verbose=verbose)


@click.group()
@click.option("--verbose", is_flag=True, help="Verbose output")
@click.pass_context
def cli(c, verbose):
    c.ensure_object(dict)
    c.obj["verbose"] = verbose


@cli.command()
@click.argument("prompt")
@click.option("--words", default=None, type=int, help="Ignored: length follows the story (kept for compatibility)")
@click.option("--project", default=None, help="Project directory (default projects/<slug>_<ts>)")
@click.option("--no-research", is_flag=True, help="Skip web research in brainstorm")
def init(prompt, words, project, no_research):
    """Create a project."""
    proj = Path(project) if project else Path("projects") / f"{slugify(prompt)[:30]}_{int(time.time())}"
    if (proj / "metadata.json").exists():
        raise click.ClickException(f"{proj} already exists")
    ctx = BookContext(proj)
    ctx.metadata = {"prompt": prompt, "created": datetime.datetime.now().isoformat(),
                    "no_research": no_research}
    ctx.save_json("metadata.json", ctx.metadata)
    click.echo(f"Project created: {proj}\nNext: bookforge run --project {proj} --step all")


@cli.command()
@click.option("--project", required=True, type=click.Path(exists=True))
@click.option("--step", default=None, help="brainstorm|outline|initialize_cast_and_world|write|critique|revise|deai|audit|finalize|all")
@click.option("--from", "from_step", default=None)
@click.option("--to", "to_step", default=None)
@click.option("--restart-from", default=None, help="Invalidate this step and everything after it (archives stale chapters)")
@click.option("--chapter", default=None, help="Chapter id, e.g. ch03")
@click.option("--force", is_flag=True, help="Rerun even if up to date; finalize without passing audit")
@click.option("--edit-effort", type=click.Choice(["low", "medium", "high"]), default=None,
              help="Reasoning effort for the editor pass (overrides config; e.g. high for a final polish)")
@click.pass_context
def run(c, project, step, from_step, to_step, restart_from, chapter, force, edit_effort):
    """Run pipeline steps."""
    if edit_effort:
        os.environ["BOOKFORGE_EDITOR_REASONING_EFFORT"] = edit_effort
    ctx = _ctx(project, c.obj["verbose"])
    if restart_from:
        progress.reset_progress_after_step(ctx, restart_from, chapter)
        if not step:
            return
    if from_step and to_step:
        progress.run_step_range(ctx, from_step, to_step, chapter)
        return
    if step == "all":
        progress.run_all_with_progress(ctx, force=force, chapters=[chapter] if chapter else None)
        return
    if not step:
        raise click.UsageError("Give --step, --from/--to, or --restart-from")
    s = progress.canonical_step(step)
    if s in progress.CHAPTER_STEPS and not chapter:
        for ch in ctx.load_outline():
            if force or not progress.is_current(ctx, s, ch["id"]):
                progress.run_step(ctx, s, ch["id"])
        return
    progress.run_step(ctx, s, chapter, **({"force": force} if s == "finalize" else {}))


@cli.command()
@click.option("--project", required=True, type=click.Path(exists=True))
def status(project):
    """Show per-step status, staleness, open work queues and spend."""
    ctx = _ctx(project)
    for s in ["brainstorm", "outline", "initialize_cast_and_world"]:
        click.echo(f"{s:28s} {'ok' if progress.is_current(ctx, s) else 'pending/stale'}")
    for ch in ctx.load_outline():
        marks = " ".join(f"{s}={'ok' if progress.is_current(ctx, s, ch['id']) else '..'}" for s in progress.CHAPTER_STEPS)
        click.echo(f"{ch['id']} {ch['title'][:30]:30s} {marks}")
    for s in ["audit", "finalize"]:
        click.echo(f"{s:28s} {'ok' if progress.is_current(ctx, s) else 'pending/stale'}")
    canon = ctx.canon()
    click.echo(f"canon v{canon.data['version']}: {len(canon.facts)} facts, {len(canon.open_threads())} open threads, "
               f"{sum(t['status'] == 'open' for t in canon.data['revision_tasks'])} open revision tasks, "
               f"{len(canon.pending_reviews())} pending reviews")
    from bookforge.core import llm
    click.echo(f"spend so far: ${llm.spent():.4f}")


@cli.group()
def canon():
    """Inspect the canon ledger."""


@canon.command("list")
@click.option("--project", required=True, type=click.Path(exists=True))
@click.option("--entity", default=None)
@click.option("--status", "status_", default=None)
@click.option("--importance", default=None)
def canon_list(project, entity, status_, importance):
    cn = _ctx(project).canon()
    for f in cn.facts.values():
        if entity and slugify(entity) not in f["entities"]:
            continue
        if status_ and f["status"] != status_:
            continue
        if importance and f["importance"] != importance:
            continue
        src = ",".join(f"{p['chapter']}:{p.get('scene')}" for p in f["provenance"]) or f.get("planned_in", "plan")
        click.echo(f"{f['id']} [{f['status']:12s} {f['importance']:8s} {f['type']}] {f['claim']}  <{src}>")


@canon.command("threads")
@click.option("--project", required=True, type=click.Path(exists=True))
def canon_threads(project):
    """Chekhov ledger: setups and payoffs."""
    for t in _ctx(project).canon().data["threads"].values():
        s = (t.get("setup_source") or {}).get("chapter", t.get("setup_chapter", "?"))
        p = (t.get("payoff_source") or {}).get("chapter", t.get("payoff_by") or "?")
        click.echo(f"{t['id']} [{t['status']:8s}] {s} -> {p}: {t['description']}")


@canon.command("tasks")
@click.option("--project", required=True, type=click.Path(exists=True))
def canon_tasks(project):
    """Revision tasks and plan amendments."""
    cn = _ctx(project).canon()
    for t in cn.data["revision_tasks"]:
        click.echo(f"{t['id']} [{t['status']}] {t['source']['chapter']}:{t['source'].get('scene')} {t['fix']}")
    for a in cn.data["amendments"]:
        click.echo(f"{a['id']} [amendment {a['kind']}] {a.get('reason', '')} updates={len(a.get('updates', []))}")


@cli.group()
def review():
    """Human review queue for low-confidence or high-stakes conflicts."""


@review.command("list")
@click.option("--project", required=True, type=click.Path(exists=True))
def review_list(project):
    for q in _ctx(project).canon().pending_reviews():
        click.echo(json.dumps({k: q[k] for k in ("id", "why_review", "conflicts_with", "explanation", "fix")} |
                              {"claim": q["fact"]["claim"], "where": f"{q['source']['chapter']}:{q['source'].get('scene')}"},
                              indent=1))


@review.command("resolve")
@click.option("--project", required=True, type=click.Path(exists=True))
@click.argument("item_id")
@click.option("--decision", type=click.Choice(["plan", "canon", "ignore"]), required=True,
              help="plan: revise the text; canon: keep the new detail and amend the plan; ignore: accept as is")
@click.option("--fix", default=None, help="Override the fix text (plan)")
def review_resolve(project, item_id, decision, fix):
    ctx = _ctx(project)
    try:
        resolve_review(ctx, item_id, decision, fix)
    except KeyError as e:
        raise click.ClickException(str(e))
    click.echo(f"{item_id} resolved: {decision}")


@cli.command()
@click.argument("path", type=click.Path(exists=True), required=False)
@click.option("--style-dir", default=None, type=click.Path(exists=True),
              help="Dir with style_bible.md/voices (allowlist + cast names)")
@click.option("--json", "as_json", is_flag=True, help="Machine-readable output")
@click.option("--fix", is_flag=True, help="Print text after safe autofix (token scrub) instead of a report")
@click.option("--fail-on", "fail_on", default=None, type=int,
              help="Exit 1 if any hit has severity >= N, any token tell remains, or fragment_budget is flagged")
def detect(path, style_dir, as_json, fix, fail_on):
    """Run the deterministic AI-tell detector on a file (or stdin). No LLM calls."""
    import sys
    from bookforge.core import detector, style_bible
    text = Path(path).read_text() if path else sys.stdin.read()
    sb = style_bible.load(Path(style_dir)) if style_dir else style_bible.StyleBible(None)
    names = [v.get("name", "") for v in sb.voices.values()] + [a for v in sb.voices.values()
                                                               for a in (v.get("aliases") or [])]
    if fix:
        click.echo(detector.autofix(text)[0])
        return
    rep = detector.detect(text, sb.allowlist(), names)
    if fail_on is not None:
        from bookforge.core.deai import count_token_tells
        click.echo(detector.family_summary(rep, text))
        bad = (any(h["severity"] >= fail_on for h in rep["hits"]) or bool(count_token_tells(text))
               or "fragment_budget" in rep.get("flags", []))
        if bad:
            raise SystemExit(1)
        return
    if as_json:
        click.echo(json.dumps({k: v for k, v in rep.items() if k != "flagged_paragraphs"}, indent=2))
    else:
        click.echo(detector.format_report(rep, max_hits=200))


@cli.command("cost")
@click.option("--project", required=True, type=click.Path(exists=True))
@click.option("--by", type=click.Choice(["chapter", "step", "role", "model"]), default="chapter")
@click.option("--chapter", default=None, help="Only this chapter")
def cost(project, by, chapter):
    """Usage report from state/usage.jsonl: calls, prompt / cached / cache-write / completion tokens,
    cache hit rate and cost, grouped by chapter, step, role or model."""
    from bookforge.core.usage import report
    click.echo(report(Path(project) / "state" / "usage.jsonl", by=by, chapter=chapter))


@cli.command()
@click.option("--project", required=True, type=click.Path(exists=True))
def costs(project):
    """Token usage and cost by role."""
    p = Path(project) / "costs.jsonl"
    agg = {}
    for line in (p.read_text().splitlines() if p.exists() else []):
        r = json.loads(line)
        a = agg.setdefault(r["role"], [0, 0, 0, 0.0])
        a[0] += 1; a[1] += r["prompt_tokens"]; a[2] += r["completion_tokens"]; a[3] += r["cost_usd"]
    for role, (n, pt, ct, usd) in sorted(agg.items()):
        click.echo(f"{role:16s} calls={n:4d} in={pt:9d} out={ct:8d} ${usd:.4f}")


@cli.command()
@click.option("--project", required=True, type=click.Path(exists=True))
def migrate(project):
    """Upgrade a v0.1 project: ch01.. ids, renamed manuscript files, canonical state, seeded canon."""
    from bookforge.core.canon_pipeline import seed_from_plan
    from bookforge.core.io import atomic_write_json, read_json
    from bookforge.core.schema import normalize_outline
    ctx = _ctx(project)
    raw = read_json(ctx.state_dir / "outline.json", []) or []
    new = normalize_outline(raw)
    mapping = {str(o.get("id")): n["id"] for o, n in zip(raw, new) if isinstance(o, dict)}
    for old, nid in mapping.items():
        if old == nid:
            continue
        for p in ctx.manuscript_dir.glob(f"{old}_*.txt"):
            p.rename(ctx.manuscript_dir / p.name.replace(old, nid, 1))
        st = ctx.progress["chapter_status"].pop(old, None)
        if st is not None:
            ctx.progress["chapter_status"][nid] = st
        click.echo(f"{old} -> {nid}")
    atomic_write_json(ctx.state_dir / "outline.json", new)
    ctx.save_characters(read_json(ctx.state_dir / "characters.json", {}) or {})
    ctx.save_world(read_json(ctx.state_dir / "world.json", {}) or {})
    ctx.save_progress()
    seed_from_plan(ctx)
    click.echo("Migrated. Steps will show as stale; run `bookforge run --step all` to rebuild what is needed.")


@cli.command("config-init")
def config_init():
    """Write a key-free config template to ~/.bookforge/config.yaml (keys stay in env vars)."""
    click.echo(f"Wrote {write_template()}")


@cli.group("screen")
def screen_group():
    """Post-draft screen: code checks + Jev, revision packet, facts files."""


@screen_group.command("run")
@click.option("--project", required=True, type=click.Path(exists=True))
@click.option("--chapter", required=True)
@click.option("--text", "text_path", default=None, type=click.Path(exists=True), help="Chapter text (default: the draft)")
@click.option("--no-jev", is_flag=True, help="Code checks only")
def screen_run(project, chapter, text_path, no_jev):
    """Screen a chapter and write its revision packet. Never calls the generative editor."""
    from bookforge.core import screen
    from bookforge.core.io import read_json
    ctx = _ctx(project)
    ch = ctx.chapter(chapter)
    if not ch:
        raise click.ClickException(f"no outline entry for {chapter}")
    text = Path(text_path).read_text() if text_path else ctx.chapter_path(chapter, "draft").read_text()
    crit = read_json(ctx.state_dir / "critiques" / f"{chapter}.json", None)
    step_dir = ctx.new_step_folder(f"screen_{chapter}")
    pk = screen.run(ctx, ch, text, crit, step_dir, use_jev=False if no_jev else None)
    est = screen.estimate(ctx, ch, text, crit, pk)
    (step_dir / "screen_estimate.json").write_text(json.dumps(est, indent=2))
    click.echo(f"{chapter}: {len(pk['flagged'])}/{pk['sections_total']} sections flagged {pk['flagged']} (jev {pk['jev']})")
    for sid in pk["flagged"]:
        for f in pk["flags"][sid]:
            p = f" p={f['p']}" if "p" in f else ""
            click.echo(f"  {sid} [{f['source']}:{f['check']}{p}] {(f.get('evidence') or f.get('detail', ''))[:110]}")
    click.echo(f"editor input: packet ~{est['packet_input_tokens']} tok vs full ~{est['full_input_tokens']} "
               f"({est['input_saving_pct']}% less); output ~{est['packet_output_tokens_est']} vs ~{est['full_output_tokens_est']}")
    click.echo(f"packet: {step_dir / 'revision_packet.md'}")


@screen_group.command("facts")
@click.option("--project", required=True, type=click.Path(exists=True))
@click.option("--chapter", required=True)
def screen_facts(project, chapter):
    """Generate or refresh the chapter facts file (keeps keys listed in _manual)."""
    from bookforge.core import facts
    ctx = _ctx(project)
    ch = ctx.chapter(chapter)
    if not ch:
        raise click.ClickException(f"no outline entry for {chapter}")
    facts.load_or_generate(ctx, ch)
    click.echo(f"wrote {facts.path(ctx, chapter)}")


from bookforge.craft.cli_cmds import craft_group  # noqa: E402

cli.add_command(craft_group)


def main():
    cli(obj={})



@cli.group("threads")
def threads_group():
    """Thread ledger: obligations per chapter, checks, and plan resync."""


@threads_group.command("show")
@click.option("--project", required=True, type=click.Path())
def threads_show(project):
    from bookforge.core import threads
    d = threads.load(BookContext(Path(project)))
    for t in d["threads"]:
        rev = sum(1 for h in t.get("history", []) if h.get("needs_review"))
        click.echo(f"{t['id']:<28} {t.get('type', ''):<8} {t.get('status', ''):<10} {t.get('open_chapter', '')!s:>6} -> "
                   f"{t.get('payoff_chapter', '')!s:<8} {'(review ' + str(rev) + ')' if rev else ''}")


@threads_group.command("plan")
@click.option("--project", required=True, type=click.Path())
@click.option("--chapter", required=True)
@click.option("--dry-run", is_flag=True, help="Print the brief and obligations; don't save the outline")
@click.option("--scenes/--no-scenes", default=True, help="Also expand scene briefs (planner)")
def threads_plan(project, chapter, dry_run, scenes):
    """Plan one chapter's thread obligations, opening hook and (optionally) scene briefs."""
    from bookforge.core import threads
    from bookforge.steps import outline as O
    ctx = BookContext(Path(project))
    outline = ctx.load_outline()
    i = next(k for k, c in enumerate(outline) if c["id"] == chapter)
    step_dir = ctx.new_step_folder(f"threadplan_{chapter}")
    O._STYLE_CTX["ctx"] = ctx
    if scenes:
        ch = O._expand(ctx.metadata["prompt"], outline, i, ctx.load_characters(), ctx.load_world(), step_dir)
    else:
        ch = threads.apply_to_chapter(dict(outline[i]), threads.plan_obligations(ctx, outline[i], outline, step_dir))
    (step_dir / f"{chapter}_plan.json").write_text(json.dumps(ch, indent=2, ensure_ascii=False))
    if not dry_run:
        outline[i] = ch
        ctx.save_outline(outline)
    click.echo(json.dumps({k: ch.get(k) for k in ("id", "title", "why_matters", "opening_hook", "thread_obligations")},
                          indent=2, ensure_ascii=False))
    for s in ch.get("scenes", []):
        click.echo(f"\nScene {s['scene_number']} @ {s.get('location', '')}: {s.get('action', '')}")
        for b in s.get("beats", []):
            click.echo(f"   - {b}")
        click.echo(f"   closing image: {s.get('closing_image', '')}")
        click.echo(f"   obligations: {[o['action'] + ':' + o['thread'] for o in s.get('thread_obligations', [])]}")
    click.echo(f"\nsaved plan -> {step_dir}" + (" (dry run: outline not changed)" if dry_run else ""))


@threads_group.command("check")
@click.option("--project", required=True, type=click.Path())
@click.option("--chapter", required=True)
@click.option("--file", "file_", type=click.Path(), default=None)
@click.option("--update/--no-update", default=True, help="Write results to the ledger (flagged for review)")
def threads_check(project, chapter, file_, update):
    from bookforge.core import threads
    ctx = BookContext(Path(project))
    ch = ctx.chapter(chapter)
    text = Path(file_).read_text() if file_ else ctx.chapter_path(chapter, "final").read_text()
    res = threads.check_obligations(ctx, ch, text)
    for r in res:
        click.echo(f"{'PASS' if r.get('pass') else 'FAIL'} {r.get('action')}:{r.get('thread')}  {r.get('quote', '')[:90]!r}  {r.get('note', '')[:120]}")
    if update:
        threads.update_ledger(ctx, ch, res)


@threads_group.command("resync")
@click.option("--project", required=True, type=click.Path())
@click.option("--chapter", required=True)
@click.option("--file", "file_", type=click.Path(), default=None, help="Chapter text (default: newest manuscript file)")
@click.option("--no-reextract", is_flag=True)
def threads_resync(project, chapter, file_, no_reextract):
    """Regenerate a chapter's stored scene plan (and canon facts) from its current text."""
    from bookforge.core import plan_sync
    ctx = BookContext(Path(project))
    text = Path(file_).read_text() if file_ else None
    ch = plan_sync.resync(ctx, chapter, text, ctx.new_step_folder(f"resync_{chapter}"), reextract=not no_reextract)
    for s in ch["scenes"]:
        click.echo(f"scene {s['scene_number']}: {s.get('action', '')[:160]}")


@cli.group("structure")
def structure_group():
    """Story structure: per-chapter function blocks (act, beats, value turn, plot function) and a lint."""


def _structure_inputs(project):
    from bookforge.core import structure as S, threads as T
    ctx = BookContext(Path(project))
    try:
        th = T.load(ctx).get("threads", [])
    except Exception:
        th = []
    return ctx, ctx.load_outline(), th, S.word_counts(ctx)


@structure_group.command("check")
@click.option("--project", required=True, type=click.Path(exists=True))
@click.option("--run-limit", default=3, show_default=True, help="Flag this many same chapter_type/ending in a row")
@click.option("--quiet", is_flag=True, help="Only FAIL and WARN (hide INFO)")
def structure_check(project, run_limit, quiet):
    """Lint the outline's structure blocks. Exit 1 if any chapter FAILs (no plot function, no value turn,
    or a function that duplicates a neighbour)."""
    from bookforge.core import structure as S
    _, outline, th, words = _structure_inputs(project)
    issues = S.check(outline, th, words, run_limit=run_limit)
    order = {"FAIL": 0, "WARN": 1, "INFO": 2}
    for i in sorted(issues, key=lambda x: (order[x["severity"]], x["chapter"])):
        if quiet and i["severity"] == "INFO":
            continue
        click.echo(f"[{i['severity']}] {i['chapter'] or '-':<5} {i['check']:<18} {i['description']}")
    n = {k: sum(1 for i in issues if i["severity"] == k) for k in order}
    click.echo(f"\n{n['FAIL']} fail, {n['WARN']} warn, {n['INFO']} info across {len(outline)} chapters")
    if n["FAIL"]:
        raise SystemExit(1)


@structure_group.command("show")
@click.option("--project", required=True, type=click.Path(exists=True))
def structure_show(project):
    """Print the structure table (act, %, beats, type, strand, value turn, ending)."""
    from bookforge.core import structure as S
    _, outline, _, words = _structure_inputs(project)
    for r in S.table(outline, words):
        click.echo(f"{r['id']:<5} {r['act']:<3} {r['percent'] or 0:>5.1f}% {r['stc'][:28]:<28} {r['hj'][:22]:<22} "
                   f"{r['type']:<11} {r['strand']:<3} {r['ending']:<15} {r['turn'][:60]}")


@structure_group.command("percent")
@click.option("--project", required=True, type=click.Path(exists=True))
@click.option("--write", is_flag=True, help="Store the computed percent in each chapter's structure block")
def structure_percent(project, write):
    """Compute each chapter's position (% of book, by word count; unwritten chapters use the mean)."""
    from bookforge.core import structure as S
    ctx, outline, _, words = _structure_inputs(project)
    pos = S.positions(outline, words)
    for c in outline:
        click.echo(f"{c['id']}  {pos[c['id']]:5.1f}%  {words.get(c['id'], 0):>6} words")
        if write:
            c.setdefault("structure", {})["percent"] = pos[c["id"]]
    if write:
        ctx.save_outline(outline)

if __name__ == "__main__":
    main()
