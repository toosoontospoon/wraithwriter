"""Resumability: a crash mid-chapter resumes at the failed scene; nothing is redone."""
import pytest

from bookforge.core import progress
from bookforge.core.context import BookContext


def test_resume_after_crash_mid_chapter(project, fake):
    state = {"boom": True}

    def write(p, s):
        if "of scene 2 of chapter ch01" in p and state["boom"]:
            state["boom"] = False
            raise RuntimeError("network died")

    fake.hooks["write"] = write
    with pytest.raises(RuntimeError):
        progress.run_all_with_progress(project)
    assert project.scene_path("ch01", 1).exists() and not project.scene_path("ch01", 2).exists()
    before_s1 = fake.count("write", "of scene 1 of chapter ch01")
    before_ext = fake.count("extract", "(ch01 scene 1)")
    progress.run_all_with_progress(BookContext(project.project_dir))
    assert fake.count("write", "of scene 1 of chapter ch01") == before_s1 == 1
    assert fake.count("extract", "(ch01 scene 1)") == before_ext + 1   # only the post-revise re-extraction
    assert (project.project_dir / "final_manuscript.txt").exists()


def test_crash_in_canon_pass_does_not_rewrite_scene(project, fake):
    state = {"boom": True}

    def extract(p, s):
        if "(ch01 scene 1)" in p and state["boom"]:
            state["boom"] = False
            raise RuntimeError("judge down")

    fake.hooks["extract"] = extract
    with pytest.raises(RuntimeError):
        progress.run_all_with_progress(project, until="write")
    progress.run_all_with_progress(BookContext(project.project_dir), until="write")
    assert fake.count("write", "of scene 1 of chapter ch01") == 1


def test_outline_phase_cache(project, fake):
    progress.run_step(project, "brainstorm")
    fake.hooks["worldbuild"] = lambda p, s: (_ for _ in ()).throw(RuntimeError("down"))
    with pytest.raises(RuntimeError):
        progress.run_step(project, "outline")
    del fake.hooks["worldbuild"]
    n = fake.count("outline", "story architect")
    progress.run_step(project, "outline")
    assert fake.count("outline", "story architect") == n == 1


def test_cli_smoke(tmp_path, fake):
    from click.testing import CliRunner
    from bookforge.cli import cli
    r = CliRunner()
    proj = tmp_path / "book"
    assert r.invoke(cli, ["init", "A boy joins an airship crew", "--words", "1800", "--project", str(proj)], obj={}).exit_code == 0
    res = r.invoke(cli, ["run", "--project", str(proj), "--step", "all"], obj={})
    assert res.exit_code == 0, res.output
    for cmd in (["status"], ["canon", "list"], ["canon", "threads"], ["canon", "tasks"], ["costs"]):
        res = r.invoke(cli, [*cmd, "--project", str(proj)], obj={})
        assert res.exit_code == 0, (cmd, res.output)
    assert "paid_off" in r.invoke(cli, ["canon", "threads", "--project", str(proj)], obj={}).output
    res = r.invoke(cli, ["run", "--project", str(proj), "--restart-from", "revise", "--chapter", "ch02"], obj={})
    assert res.exit_code == 0 and "Progress reset" in res.output


def test_migrate_legacy_project(tmp_path):
    import shutil
    from pathlib import Path
    from click.testing import CliRunner
    from bookforge.cli import cli
    src = Path(__file__).resolve().parent.parent / "projects" / "test_novella"
    if not src.exists():
        pytest.skip("sample project not present")
    dst = tmp_path / "legacy"
    shutil.copytree(src, dst)
    res = CliRunner().invoke(cli, ["migrate", "--project", str(dst)], obj={})
    assert res.exit_code == 0, res.output
    ctx = BookContext(dst)
    assert [c["id"] for c in ctx.load_outline()][:2] == ["ch01", "ch02"]
    assert (dst / "manuscript" / "ch05_draft.txt").exists()
    assert ctx.canon().entities
