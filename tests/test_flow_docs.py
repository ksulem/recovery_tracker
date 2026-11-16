"""docs/FLOWS.md is generated from the flows; it must not drift from the code."""

from pathlib import Path

from recovery import flowdocs
from recovery.conversation.flows import FLOWS, VARIANTS

DOC = Path(__file__).resolve().parent.parent / "docs" / "FLOWS.md"


def test_every_question_has_a_short_name() -> None:
    ids = {q.id for flow in [*FLOWS.values(), *VARIANTS.values()] for q in flow.questions}
    missing = sorted(ids - flowdocs.SHORT.keys())
    assert not missing, f"add these question ids to flowdocs.SHORT: {missing}"


def test_flow_diagrams_are_up_to_date() -> None:
    assert DOC.read_text(encoding="utf-8") == flowdocs.render(), (
        "docs/FLOWS.md is out of date: run `.venv/bin/python -m recovery.flowdocs > docs/FLOWS.md`"
    )
