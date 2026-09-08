"""The worker routes every job type it can be sent, on BOTH paths.

queue_worker has two dispatch chains -- one for the envelope format and a legacy
one -- and they are separate if/elif ladders over the same job types. Adding
`grocery_match` to the legacy chain and not the envelope one produced a job that
enqueued fine, ran, and failed with "Unknown job type in envelope", which looks
like a queue problem rather than a missing branch.

These read the source rather than executing it. Running the real chains would
need Redis, a database session and a live LLM for each type; what actually goes
wrong here is a branch that was never written, and that is visible statically.
"""
import ast
from pathlib import Path

import pytest

WORKER = Path(__file__).resolve().parents[1] / "jarvis_recipes/app/services/queue_worker.py"

# Types that can arrive on EITHER path. The two chains also carry names unique to
# their own format ("ocr.completed" is envelope-only; "image" is legacy-only),
# which is why this is a list and not an equality check between the two.
SHARED_JOB_TYPES = {"ingestion", "meal_plan_generate", "grocery_match"}


def _dispatch_chains() -> list[set[str]]:
    """The job_type literals routed by each dispatch ladder.

    A ladder is identified by how it ENDS -- a final else that reports
    "unknown_job_type" -- rather than by counting branches. `ast.walk` also
    reaches every nested `if` inside a handler, and those are ordinary control
    flow, not routing.
    """
    tree = ast.parse(WORKER.read_text())
    chains: list[set[str]] = []

    # `elif` is an If nested in the previous If's orelse, and ast.walk visits
    # every rung. Without this the same ladder is counted once per branch.
    rungs = {
        id(node.orelse[0])
        for node in ast.walk(tree)
        if isinstance(node, ast.If)
        and len(node.orelse) == 1
        and isinstance(node.orelse[0], ast.If)
    }

    for node in ast.walk(tree):
        if not isinstance(node, ast.If) or id(node) in rungs:
            continue
        types: set[str] = set()
        current: ast.If | None = node
        tail: list[ast.stmt] = []
        while current is not None:
            test = current.test
            if (
                isinstance(test, ast.Compare)
                and isinstance(test.left, ast.Name)
                and test.left.id == "job_type"
                and isinstance(test.comparators[0], ast.Constant)
            ):
                types.add(test.comparators[0].value)
            tail = current.orelse
            current = tail[0] if len(tail) == 1 and isinstance(tail[0], ast.If) else None

        terminates_in_unknown = any(
            isinstance(sub, ast.Constant) and sub.value == "unknown_job_type"
            for stmt in tail
            for sub in ast.walk(stmt)
        )
        if types and terminates_in_unknown:
            chains.append(types)

    return chains


def test_there_are_exactly_two_dispatch_chains():
    """If this fails, a third has appeared -- or the two were merged.

    Merging them is the better outcome and this test should be deleted with the
    duplication. Until then, knowing how many there are is what makes the check
    below meaningful.
    """
    assert len(_dispatch_chains()) == 2, (
        "queue_worker's job_type dispatch shape changed; update this test"
    )


@pytest.mark.parametrize("job_type", sorted(SHARED_JOB_TYPES))
def test_every_shared_job_type_is_handled_on_both_paths(job_type):
    chains = _dispatch_chains()
    missing = [i for i, chain in enumerate(chains) if job_type not in chain]
    assert not missing, (
        f"{job_type!r} is missing from dispatch chain(s) {missing}. "
        "A job enqueued for it will run and fail with 'Unknown job type'."
    )


def test_the_handler_for_each_shared_type_exists():
    """A branch that calls a function that is not there fails at runtime only."""
    tree = ast.parse(WORKER.read_text())
    defined = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}

    for job_type in SHARED_JOB_TYPES:
        handler = f"_process_{job_type}_job".replace("meal_plan_generate", "meal_plan")
        assert handler in defined, f"No handler {handler} for {job_type}"
