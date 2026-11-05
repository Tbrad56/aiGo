from aigo.store import Store


def test_collections_crud_and_persistence(tmp_path):
    path = tmp_path / "data.json"
    s = Store(path)
    r = s.add("routes", {"name": "Gym run", "stops": [{"lat": 1, "lon": 2, "wait_min": 0}]})
    assert s.get("routes", r["id"])["name"] == "Gym run"
    s.update("routes", r["id"], {"name": "Gym loop", "stops": []})
    assert Store(path).get("routes", r["id"])["name"] == "Gym loop"  # saved to disk
    assert s.update("routes", "nope", {}) is None
    assert s.remove("routes", r["id"]) is True
    assert s.remove("routes", r["id"]) is False
    assert s.items("routes") == []


def test_old_file_without_new_collections_loads(tmp_path):
    path = tmp_path / "data.json"
    path.write_text('{"favorites": [{"id": "a", "name": "x", "lat": 1, "lon": 2}], "history": []}')
    s = Store(path)
    assert s.favorites()[0]["id"] == "a"
    assert s.items("schedules") == []


def test_items_are_copies(tmp_path):
    s = Store(tmp_path / "d.json")
    s.add("schedules", {"time": "09:00"})
    s.items("schedules")[0]["time"] = "10:00"
    assert s.items("schedules")[0]["time"] == "09:00"
