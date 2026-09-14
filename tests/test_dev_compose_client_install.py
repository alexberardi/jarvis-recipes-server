"""The dev compose must not install the project and the local clients together.

pyproject pins the shared clients by git URL:

    jarvis-config-client @ git+https://github.com/.../jarvis-config-client.git@fea99b5

The source-dev quickstart bind-mounts those same repos and installs them
editable so local edits take effect. Put both in ONE pip invocation and pip has
to reconcile two different *sources* for one name -- the editable path and the
wheel built from that git URL -- at the same version:

    The user requested jarvis-config-client 0.2.1 (from editable /jarvis-config-client)
    jarvis-recipes 0.1.0 depends on jarvis-config-client 0.2.1 (from .../jarvis_config_client-0.2.1-py3-none-any.whl)
    ERROR: ResolutionImpossible

Older pip tolerated it. The Dockerfile runs `pip install --upgrade pip`, so the
day a stricter pip shipped, every source-dev boot of this service died in the
install step -- the container exited 1 before uvicorn ever ran, and
install-e2e-quickstart went from green on 2026-09-07 to
`test_service_healthy[recipes-server]: assert None == 200` every night after.
Nothing in this repo changed; the pin was months old.

Two separate invocations are two separate resolutions, so neither has to
reconcile anything: the project brings its pinned clients, then the editables
replace them, which is the override the mounts exist to provide.
"""
import re
from pathlib import Path

import pytest

COMPOSE = Path(__file__).resolve().parent.parent / "docker-compose.dev.yaml"

# `command: bash -c "..."` -- capture the quoted script for each service.
_COMMANDS = re.compile(r'command:\s*bash\s+-c\s+"([^"]+)"')


def _pip_invocations() -> list[str]:
    """Every individual pip call in the dev compose, split on && and ;."""
    calls = []
    for script in _COMMANDS.findall(COMPOSE.read_text()):
        for part in re.split(r"&&|;", script):
            part = part.strip()
            if part.startswith("pip install"):
                calls.append(part)
    return calls


def test_the_dev_compose_still_installs_with_pip() -> None:
    # If this fails the file was restructured and the guard below is watching
    # nothing -- which is how the original bug would sail back in.
    assert _pip_invocations(), f"no pip install found in {COMPOSE.name}"


@pytest.mark.parametrize("client", ["jarvis-config-client", "jarvis-settings-client"])
def test_a_local_client_is_never_installed_alongside_the_project(client: str) -> None:
    for call in _pip_invocations():
        installs_project = re.search(r"(?<![\w./])-e\s+\.(?!\w)", call) is not None
        installs_client = f"/{client}" in call
        assert not (installs_project and installs_client), (
            f"one pip call installs both the project and editable {client}, "
            f"which pip rejects as ResolutionImpossible: {call!r}"
        )


def test_the_local_clients_are_still_installed_editable() -> None:
    # The whole point of the mounts. Fixing the conflict by dropping the
    # editable clients would make local client edits invisible.
    joined = " ".join(_pip_invocations())
    for client in ("jarvis-config-client", "jarvis-settings-client"):
        assert re.search(rf"-e\s+/{client}", joined), f"{client} is no longer installed editable"


def test_the_project_itself_is_still_installed() -> None:
    joined = " ".join(_pip_invocations())
    assert re.search(r"(?<![\w./])-e\s+\.(?!\w)", joined), "the project is no longer installed"
