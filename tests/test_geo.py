import pytest

from aigo.geo import bearing_deg, destination, distance_m, path_length_m, route_positions

TIMES_SQUARE = (40.7580, -73.9855)
EMPIRE_STATE = (40.7484, -73.9857)


def test_distance_known_pair():
    # Times Square to the Empire State Building is roughly 1.07 km.
    assert distance_m(TIMES_SQUARE, EMPIRE_STATE) == pytest.approx(1070, rel=0.02)


def test_distance_zero():
    assert distance_m(TIMES_SQUARE, TIMES_SQUARE) == 0


def test_bearing_due_north_and_east():
    assert bearing_deg((0, 0), (1, 0)) == pytest.approx(0)
    assert bearing_deg((0, 0), (0, 1)) == pytest.approx(90)


def test_destination_round_trip():
    p = destination(TIMES_SQUARE, 45, 500)
    assert distance_m(TIMES_SQUARE, p) == pytest.approx(500, rel=1e-3)
    assert bearing_deg(TIMES_SQUARE, p) == pytest.approx(45, abs=0.1)


def test_destination_wraps_longitude():
    _, lon = destination((0, 179.999), 90, 1000)
    assert -180 <= lon <= 180


def test_route_positions_spacing_and_end():
    pts = route_positions([TIMES_SQUARE, EMPIRE_STATE], 100)
    assert pts[-1] == EMPIRE_STATE
    # ~1070 m at 100 m steps -> 10 full steps plus the final point
    assert len(pts) == 11
    assert distance_m(TIMES_SQUARE, pts[0]) == pytest.approx(100, rel=1e-3)


def test_route_positions_carries_across_segments():
    a, b = (0.0, 0.0), destination((0.0, 0.0), 90, 150)
    c = destination(b, 0, 150)
    pts = route_positions([a, b, c], 100)
    # 300 m total -> positions at 100, 200, 300
    assert len(pts) == 3
    assert path_length_m([a, b]) == pytest.approx(150, rel=1e-3)
    assert distance_m(pts[-1], c) < 1e-6


def test_route_positions_single_point():
    assert route_positions([TIMES_SQUARE], 10) == [TIMES_SQUARE]


def test_route_positions_rejects_bad_step():
    with pytest.raises(ValueError):
        route_positions([TIMES_SQUARE, EMPIRE_STATE], 0)


def test_walk_path_uses_each_step_from_the_function():
    from aigo.geo import walk_path

    a, b = (0.0, 0.0), destination((0.0, 0.0), 90, 100)
    steps = iter([10, 20, 30])
    pts = list(walk_path([a, b], lambda: next(steps, 40)))
    # 10, 30, 60, then 40 more would overshoot 100 -> final point
    got = [round(distance_m(a, p)) for p in pts]
    assert got == [10, 30, 60, 100]
    assert distance_m(pts[-1], b) < 1e-6


def test_walk_path_matches_route_positions_for_constant_step():
    from aigo.geo import walk_path

    pts = [TIMES_SQUARE, EMPIRE_STATE, destination(EMPIRE_STATE, 120, 333)]
    assert list(walk_path(pts, lambda: 50)) == route_positions(pts, 50)


def test_walk_path_rejects_non_positive_step():
    from aigo.geo import walk_path

    with pytest.raises(ValueError):
        list(walk_path([TIMES_SQUARE, EMPIRE_STATE], lambda: 0))

