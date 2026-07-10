"""Тесты формирования данных графика мойки (webapp/chart_payload)."""
import math

from webapp.chart_payload import MAX_CHART_POINTS, _downsample_points


def test_downsample_keeps_extremes_within_limit():
    # Пик и провал не должны выбрасываться наивной децимацией
    points = [[float(i), math.sin(i / 50.0) * 10.0] for i in range(10000)]
    points[3333][1] = 999.0  # одиночный пик
    points[7777][1] = -999.0  # одиночный провал

    reduced = _downsample_points(points)

    assert len(reduced) <= MAX_CHART_POINTS
    values = [point[1] for point in reduced]
    assert 999.0 in values
    assert -999.0 in values
    # первая и последняя точки сохраняются
    assert reduced[0] == points[0]
    assert reduced[-1] == points[-1]


def test_downsample_short_series_untouched():
    points = [[float(i), float(i)] for i in range(10)]
    assert _downsample_points(points) == points
