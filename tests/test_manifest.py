"""The manifest is the guarantee. These tests are about it holding."""

import pytest
from pydantic import ValidationError

from claudette import manifest_path
from claudette.manifest import Manifest, Work, load_manifest


def test_shipped_manifest_loads_and_every_work_names_its_author():
    m = load_manifest(manifest_path())
    assert len(m.works) >= 30
    for w in m.works:
        assert w.author.strip(), w.slug
        assert w.why.strip(), w.slug
        assert w.year < 1930, f"{w.slug}: public-domain scope"
    assert len(m.authors()) >= 25


def test_shipped_manifest_ids_and_slugs_are_unique():
    m = load_manifest(manifest_path())
    assert len({w.id for w in m.works}) == len(m.works)
    assert len({w.slug for w in m.works}) == len(m.works)


def test_missing_author_does_not_load():
    with pytest.raises(ValidationError):
        Work(id=1, slug="x", author="", title="T", year=1900, shelf="thought", why="w")


def test_duplicate_id_is_rejected():
    a = Work(id=1, slug="a", author="A", title="T", year=1900, shelf="thought", why="w")
    b = Work(id=1, slug="b", author="B", title="T", year=1900, shelf="thought", why="w")
    with pytest.raises(ValidationError, match="duplicate Gutenberg ids"):
        Manifest(name="n", source="s", principle="p", works=[a, b])


def test_slug_shape_is_enforced():
    with pytest.raises(ValidationError):
        Work(id=1, slug="Not A Slug", author="A", title="T", year=1900, shelf="thought", why="w")
