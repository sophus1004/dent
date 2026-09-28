"""2차원으로 줄이기 테스트: 모양 · 범위, 같은 벡터면 같은 좌표, 쓴 값 기록, 점이 몇 개뿐일 때."""

import numpy as np

from dent.system import projection

# 테스트는 작은 데이터에 적은 반복으로 빨리 돈다.
FAST_EPOCHS = 30


def _vectors(count: int, *, dim: int = 16) -> np.ndarray:
    """무리 두 개로 나뉘는 벡터들."""
    rng = np.random.default_rng(0)
    centers = np.stack([np.ones(dim), -np.ones(dim)])
    labels = np.arange(count) % 2
    return (centers[labels] + rng.normal(scale=0.3, size=(count, dim))).astype(np.float64)


def test_project_2d_returns_float32_coords_inside_unit_square():
    # 실행
    result = projection.project_2d(_vectors(40), n_epochs=FAST_EPOCHS)

    # 확인
    assert result.coords.shape == (40, 2)
    assert result.coords.dtype == np.float32
    assert np.abs(result.coords).max() <= 1.0 + 1e-6
    assert np.isclose(np.abs(result.coords).max(), 1.0)


def test_project_2d_is_reproducible_for_same_vectors():
    # 실행
    first = projection.project_2d(_vectors(30), n_epochs=FAST_EPOCHS)
    second = projection.project_2d(_vectors(30), n_epochs=FAST_EPOCHS)

    # 확인
    np.testing.assert_array_equal(first.coords, second.coords)


def test_project_2d_records_parameters_used():
    # 실행
    result = projection.project_2d(_vectors(10), n_epochs=FAST_EPOCHS)

    # 확인
    assert result.params == {
        "method": "umap",
        "n_neighbors": 9,
        "min_dist": projection.UMAP_MIN_DIST,
        "metric": "cosine",
        "random_state": projection.UMAP_RANDOM_STATE,
        "n_epochs": FAST_EPOCHS,
    }


def test_project_2d_places_few_points_on_circle_without_umap():
    # 실행
    none = projection.project_2d(np.empty((0, 4)))
    one = projection.project_2d(np.ones((1, 4)))
    three = projection.project_2d(np.ones((3, 4)))

    # 확인
    assert none.coords.shape == (0, 2)
    assert one.coords.tolist() == [[0.0, 0.0]]
    assert three.params == {"method": "circle"}
    np.testing.assert_allclose(np.linalg.norm(three.coords, axis=1), 1.0, atol=1e-6)
