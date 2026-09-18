from claudette.bootstrap import _current_format


def test_current_index_is_recognised(fixture_home):
    assert _current_format(fixture_home / "claudette.db")


def test_old_or_garbage_index_is_not(tmp_path):
    import sqlite3

    old = tmp_path / "old.db"
    con = sqlite3.connect(old)
    con.execute("CREATE TABLE works (slug TEXT)")  # v0.1.0 had no meta table
    con.commit()
    con.close()
    assert not _current_format(old)
    junk = tmp_path / "junk.db"
    junk.write_bytes(b"not a database")
    assert not _current_format(junk)
