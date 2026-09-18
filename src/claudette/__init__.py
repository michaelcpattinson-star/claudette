"""Claudette: an assistant that answers only from women-authored texts.

The package is deliberately layered so that the guarantee it makes — every
passage came from a work by a named woman — is enforced by data and
structure, not by a prompt:

    data/manifest.toml   the curated list of works. The only thing that
                         decides what gets indexed.
    manifest.py          loads and validates it.
    fetch.py             downloads each work and strips Gutenberg boilerplate.
    chunk.py             splits a text into citeable passages.
    index.py             builds and queries the SQLite FTS5 index.
    envelope.py          the uniform response shape, with provenance the
                         caller cannot omit.
    bootstrap.py         gets an index onto disk on first run.
    server.py            the MCP server (holds no key, calls no network
                         after bootstrap).
    chat.py              a chat client. The only module that talks to a model.
"""

from __future__ import annotations

import os
from pathlib import Path

__version__ = "0.1.0"


def manifest_path() -> Path:
    """The manifest ships inside the package, so an installed wheel is self-describing."""
    return Path(__file__).resolve().parent / "data" / "manifest.toml"


def home_dir() -> Path:
    """Where fetched texts and the built index live. Override with CLAUDETTE_HOME."""
    env = os.environ.get("CLAUDETTE_HOME")
    p = Path(env).expanduser() if env else Path.home() / ".claudette"
    p.mkdir(parents=True, exist_ok=True)
    return p


def texts_dir() -> Path:
    return home_dir() / "texts"


def db_path() -> Path:
    return home_dir() / "claudette.db"
