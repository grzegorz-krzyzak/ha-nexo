"""Translation files stay parseable by the frontend."""

import json
from pathlib import Path

import pytest

TRANSLATIONS = Path(__file__).parent.parent / "custom_components/nexo/translations"


def _strings(node, path=""):
    if isinstance(node, str):
        yield path, node
    else:
        for key, value in node.items():
            yield from _strings(value, f"{path}.{key}" if path else key)


@pytest.mark.parametrize("file", sorted(TRANSLATIONS.glob("*.json")), ids=lambda f: f.name)
def test_no_markup_in_translations(file: Path) -> None:
    """The frontend reads '<' as an ICU tag, and a tag with attributes fails
    with INVALID_TAG - markup has to come in through placeholders."""
    offending = [path for path, text in _strings(json.loads(file.read_text())) if "<" in text]
    assert offending == []


def test_languages_have_the_same_keys() -> None:
    files = sorted(TRANSLATIONS.glob("*.json"))
    keys = [{path for path, _ in _strings(json.loads(f.read_text()))} for f in files]
    assert all(k == keys[0] for k in keys)
