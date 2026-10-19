"""BookForge web UI (streamlit run webui.py).

Steps run in a background thread with a live log, edits go to the canonical
state/ and manuscript/ files the steps actually read, and the canon review queue
can be resolved here.
"""
import contextlib
import io
import json
import threading
from pathlib import Path

import streamlit as st

from bookforge.core import progress
from bookforge.core.canon_pipeline import resolve_review
from bookforge.core.context import BookContext
from bookforge.core.schema import slugify

st.set_page_config(page_title="BookForge", layout="wide")
PROJECTS = Path("projects")


@st.cache_resource
def jobs():
    return {}


def start_job(project, step, chapter, force):
    log = io.StringIO()

    def work():
        with contextlib.redirect_stdout(log):
            try:
                ctx = BookContext(Path(project))
                if step == "all":
                    progress.run_all_with_progress(ctx, force=force)
                else:
                    progress.run_step(ctx, step, chapter or None, **({"force": force} if step == "finalize" else {}))
                print("DONE")
            except Exception as e:  # surfaced in the log panel
                print(f"ERROR: {type(e).__name__}: {e}")
    t = threading.Thread(target=work, daemon=True)
    jobs()[project] = {"thread": t, "log": log, "step": step}
    t.start()


st.sidebar.title("BookForge")
projects = sorted(p.name for p in PROJECTS.iterdir() if p.is_dir()) if PROJECTS.exists() else []
sel = st.sidebar.selectbox("Project", [""] + projects)
with st.sidebar.expander("New project"):
    prompt = st.text_input("Prompt")
    words = st.number_input("Target words", value=80000, min_value=5000)
    if st.button("Create") and prompt:
        proj = PROJECTS / slugify(prompt)[:30]
        if (proj / "metadata.json").exists():
            st.error(f"{proj} already exists")
        else:
            ctx = BookContext(proj)
            ctx.metadata = {"prompt": prompt}  # story-first: no word target
            ctx.save_json("metadata.json", ctx.metadata)
            st.success(f"Created {proj}")
            st.rerun()

if not sel:
    st.write("Select or create a project.")
    st.stop()

project = str(PROJECTS / sel)
ctx = BookContext(Path(project))
st.title(ctx.metadata.get("title") or sel)
tab_run, tab_edit, tab_canon, tab_craft, tab_review = st.tabs(["Run", "Edit", "Canon", "Craft", "Review queue"])

with tab_run:
    step = st.selectbox("Step", ["all"] + progress.STEP_ORDER)
    chapter = st.selectbox("Chapter", [""] + [c["id"] for c in ctx.load_outline()]) if step in progress.CHAPTER_STEPS else ""
    force = st.checkbox("Force")
    job = jobs().get(project)
    running = job and job["thread"].is_alive()
    if st.button("Run", disabled=bool(running)):
        start_job(project, step, chapter, force)
        st.rerun()
    if job:
        st.caption(f"{job['step']}: {'running' if running else 'finished'}")
        st.code(job["log"].getvalue()[-6000:] or "(no output yet)")
        if running and st.button("Refresh"):
            st.rerun()
    st.subheader("Status")
    rows = [{"chapter": c["id"], "title": c["title"],
             **{s: "ok" if progress.is_current(ctx, s, c["id"]) else "" for s in progress.CHAPTER_STEPS}}
            for c in ctx.load_outline()]
    st.dataframe(rows)

with tab_edit:
    files = [p for p in sorted(ctx.state_dir.glob("*.json")) + sorted(ctx.manuscript_dir.rglob("*.txt"))]
    f = st.selectbox("File", [str(p.relative_to(ctx.project_dir)) for p in files])
    if f:
        path = ctx.project_dir / f
        txt = st.text_area(f, path.read_text(), height=500)
        if st.button("Save"):
            if path.suffix == ".json":
                try:
                    json.loads(txt)
                except json.JSONDecodeError as e:
                    st.error(f"Invalid JSON: {e}")
                    st.stop()
            ctx.write_text(path, txt)
            st.success("Saved. Downstream steps are now stale and will rerun.")

with tab_canon:
    cn = ctx.canon()
    st.caption(f"canon v{cn.data['version']}")
    status = st.multiselect("Status", ["planned", "established", "contradicted", "retired"], ["established"])
    st.dataframe([{"id": x["id"], "status": x["status"], "importance": x["importance"], "type": x["type"],
                   "claim": x["claim"], "entities": ", ".join(x["entities"])}
                  for x in cn.facts.values() if x["status"] in status])
    st.subheader("Threads (setups / payoffs)")
    st.dataframe(list(cn.data["threads"].values()))
    st.subheader("Revision tasks")
    st.dataframe(cn.data["revision_tasks"])
    st.subheader("Plan amendments")
    st.json(cn.data["amendments"])

with tab_craft:
    from bookforge.craft import engine as craft_engine
    from bookforge.craft.views import ledger_rows, tension_series, timeline
    L = craft_engine.ledger(ctx)
    if not L.has_plan:
        st.write("No craft plan yet (built during outline, or run `bookforge craft plan`).")
        if st.button("Build craft plan"):
            craft_engine.plan_all(ctx)
            st.rerun()
    else:
        st.caption(f"framework: {L.data['framework']} | ledger v{L.data['version']} | "
                   f"{len(L.data['planned'])} planned events, {len(L.data['realized'])} realized")
        keys = [f"{c['id']}:{x.get('scene_number')}" for c in ctx.load_outline() for x in c.get("scenes") or []]
        series = tension_series(L, keys)
        st.subheader("Tension: planned vs realized")
        st.line_chart({"planned": [r["planned"] for r in series],
                       "realized": [r["realized"] if r["realized"] is not None else float("nan") for r in series]})
        st.subheader("Timeline (P planned, R realized, X both)")
        st.dataframe(timeline(L, [c["id"] for c in ctx.load_outline()]))
        which = st.selectbox("Ledger", list(L.data["modules"]) + ["cards", "events"])
        st.dataframe(ledger_rows(L, which))
        st.subheader("Plan checks")
        st.dataframe(L.data["plan_issues"])
        if st.button("Run craft audit checks"):
            st.dataframe(craft_engine.audit(ctx))

with tab_review:
    for q in ctx.canon().pending_reviews():
        with st.container(border=True):
            st.markdown(f"**{q['fact']['claim']}**  ({q['source']['chapter']} scene {q['source'].get('scene')})")
            st.write(f"Why: {q.get('why_review')}. {q['explanation']}")
            st.write(f"Conflicts with: {q['conflicts_with']}  | suggested fix: {q.get('fix')}")
            c1, c2, c3 = st.columns(3)
            for col, d in ((c1, "plan"), (c2, "canon"), (c3, "ignore")):
                if col.button(f"{d} wins" if d != "ignore" else "accept", key=q["id"] + d):
                    resolve_review(ctx, q["id"], d)
                    st.rerun()
