import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def pins(path: Path) -> dict[str, str]:
    result = {}
    for line in path.read_text().splitlines():
        line = line.split("#")[0].strip()
        if not line or line.startswith("-"):
            continue
        name, version = re.fullmatch(r"([A-Za-z0-9_.-]+)(?:\[[^\]]*\])?==(\S+)", line).groups()
        result[re.sub(r"[-_.]+", "-", name).lower()] = version
    return result


def test_lock_pins_every_direct_requirement_at_the_same_version():
    direct = pins(ROOT / "requirements.txt")
    locked = pins(ROOT / "requirements.lock")
    assert {name: locked.get(name) for name in direct} == direct, "run `make lock` after changing requirements.txt"


def test_lock_pins_transitive_packages_too():
    locked = pins(ROOT / "requirements.lock")
    assert {"starlette", "anyio", "pydantic-core", "typing-extensions"} <= locked.keys()
