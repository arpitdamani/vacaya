from datetime import date

import db


def test_save_list_load_roundtrip(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB", tmp_path / "t.db")
    pid = db.save("Madrid -> Paris", {"depart": date(2026, 10, 10)}, "# md", 950.0, "USD")
    assert db.list_plans()[0][0] == pid
    assert db.list_plans()[0][2:] == ("Madrid -> Paris", 950.0, "USD")
    assert db.load(pid) == "# md"
