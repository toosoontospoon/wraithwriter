import json

from pydantic import Field

from bookforge.config import load_config
from bookforge.core import progress
from bookforge.core.io import read_json, atomic_write_json
from bookforge.core.llm import llm_call, web_search_call
from bookforge.core.schema import _Loose


class BrainstormResult(_Loose):
    genre: str = "fiction"
    themes: list[str] = Field(default_factory=list)
    why_they_work: str = ""


def run(ctx):
    inputs = progress.input_hash(ctx, "brainstorm")
    step_dir = ctx.new_step_folder("brainstorm")
    (step_dir / "input_snapshot.json").write_text(json.dumps(ctx.metadata, indent=2))
    prompt = ctx.metadata["prompt"]

    research = {"text": "", "sources": []}
    cache = ctx.state_dir / "web_research.json"
    if load_config()["brainstorm"].get("web_research", True) and not ctx.metadata.get("no_research"):
        research = read_json(cache, None)
        if not research or research.get("prompt") != prompt:
            print("Researching similar bestsellers...")
            research = web_search_call(
                f"Find the most popular and bestselling novels similar to this premise: '{prompt}'. Focus on "
                "recent bestsellers and reader favorites in the same genre. List the top 10 with title, author, "
                "and why each was successful.")
            research["prompt"] = prompt
            atomic_write_json(cache, research)
    (step_dir / "web_research.json").write_text(json.dumps(research, indent=2))

    result = llm_call(f"""You are a book market analyst. Extract commercially strong themes and tropes for this premise.

USER PREMISE: {prompt}

MARKET RESEARCH:
{research.get('text', '')[:6000] or '(none, use your own knowledge)'}

Suggest 6-8 themes or tropes that fit the premise. Output ONLY JSON:
{{"genre": "primary genre / subgenre", "themes": ["..."], "why_they_work": "brief explanation"}}""",
                      role="brainstorm", schema=BrainstormResult, raw_dir=step_dir).model_dump()

    persona = llm_call(f"""You are a creative writing coach. Describe the ideal author persona for this book in one paragraph of 200-300 words: style, narrative person and tense, tone, influences, and concrete techniques. Write in plain sentences without dashes.

PREMISE: {prompt}
GENRE: {result['genre']}
THEMES: {json.dumps(result['themes'])}""", max_tokens=800, role="brainstorm")
    result["persona"] = persona.strip()
    (step_dir / "output.json").write_text(json.dumps(result, indent=2))
    ctx.metadata["brainstorm"] = result
    ctx.save_json("metadata.json", ctx.metadata)
    progress.record(ctx, "brainstorm", inputs=inputs)
    print(f"Brainstorm complete -> {step_dir}\n   Genre: {result['genre']}")
