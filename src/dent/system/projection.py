"""임베딩 벡터를 2차원 좌표로 줄이기 (의미 지도의 바탕). 모듈 종류와 상관없이 쓴다.

UMAP(umap-learn)을 쓴다. 뜻이 가까운 문장이 가까이 모이고, 먼 무리끼리는 떨어지게 놓는다.
- 거리: cosine. 문장 임베딩은 방향이 뜻이라서 길이보다 각도로 견준다.
- random_state를 고정해 같은 벡터면 같은 지도가 나오게 한다(대신 한 코어로만 돈다).
- 벡터는 float32로 받는다. 수십만 × 1024차원이면 float64로는 메모리가 두 배다.
- 좌표는 가운데를 0에 두고 가로세로 비율을 지킨 채 [-1, 1] 안에 맞춘다. 화면이 그대로 그린다.
쓴 값은 Projection.params에 남긴다(지도에 적어 두려고).

umap은 불러오는 데만 몇 초 걸린다(numba 컴파일). API 서버가 실행될 때 느려지지 않게 쓸 때 불러온다.
"""

import math
from dataclasses import dataclass
from typing import Any

import numpy as np

# 이웃 수. 작으면 작은 무리를, 크면 전체 모양을 더 살린다. umap 기본값.
UMAP_N_NEIGHBORS = 15

# 점 사이 최소 거리. 작을수록 무리가 촘촘하게 뭉친다. umap 기본값.
UMAP_MIN_DIST = 0.1

# 거리 재는 방법
UMAP_METRIC = "cosine"

# 같은 벡터면 같은 지도가 나오게 고정하는 씨앗 값
UMAP_RANDOM_STATE = 42

# UMAP이 도는 최소 점 수. 이보다 적으면 umap의 첫 배치(스펙트럼)가 풀리지 않는다.
UMAP_MIN_POINTS = 4

# 줄인 차원 수 (지도라서 2)
N_COMPONENTS = 2

# 좌표를 맞출 범위의 끝. 화면(regl-scatterplot)은 [-1, 1]을 그대로 그린다.
COORD_LIMIT = 1.0


@dataclass(frozen=True)
class Projection:
    """2차원으로 줄인 결과."""

    # 좌표 (점 수, 2) float32. 가운데가 0이고 [-1, 1] 안에 있다.
    coords: np.ndarray

    # 쓴 값: method · n_neighbors · min_dist · metric · random_state · n_epochs
    params: dict[str, Any]


def project_2d(
    vectors: np.ndarray,
    *,
    n_neighbors: int = UMAP_N_NEIGHBORS,
    min_dist: float = UMAP_MIN_DIST,
    n_epochs: int | None = None,
) -> Projection:
    """벡터 (점 수, 차원)를 2차원 좌표로 줄인다. n_epochs가 None이면 umap이 점 수에 맞춰 고른다."""
    matrix = np.asarray(vectors, dtype=np.float32)
    count = matrix.shape[0]
    if count < UMAP_MIN_POINTS:
        # 점이 몇 개뿐이면 지도로 볼 것이 없다. 원 위에 고르게 놓는다.
        return Projection(coords=_circle(count), params={"method": "circle"})

    # 쓸 때 불러온다(위 설명).
    import umap

    # 이웃 수는 점 수보다 작아야 한다.
    used_neighbors = min(n_neighbors, count - 1)
    reducer = umap.UMAP(
        n_components=N_COMPONENTS,
        n_neighbors=used_neighbors,
        min_dist=min_dist,
        metric=UMAP_METRIC,
        random_state=UMAP_RANDOM_STATE,
        # 씨앗 값을 고정하면 umap은 어차피 한 코어로만 돈다. 그렇다고 적어 두어 경고를 없앤다.
        n_jobs=1,
        n_epochs=n_epochs,
    )
    coords = np.asarray(reducer.fit_transform(matrix), dtype=np.float32)
    return Projection(
        coords=_fit_to_limit(coords),
        params={
            "method": "umap",
            "n_neighbors": used_neighbors,
            "min_dist": min_dist,
            "metric": UMAP_METRIC,
            "random_state": UMAP_RANDOM_STATE,
            "n_epochs": n_epochs,
        },
    )


def _fit_to_limit(coords: np.ndarray) -> np.ndarray:
    """가운데를 0에 두고, 가로세로 비율을 지킨 채 [-1, 1] 안에 맞춘다."""
    low = coords.min(axis=0)
    high = coords.max(axis=0)
    centered = coords - (low + high) / 2
    half_range = float(np.abs(centered).max())
    # 모든 점이 한 자리에 있으면 나눌 수 없다. 가운데에 둔다.
    if half_range == 0:
        return np.zeros_like(coords)
    return (centered / half_range * COORD_LIMIT).astype(np.float32)


def _circle(count: int) -> np.ndarray:
    """점 count개를 원 위에 고르게 놓는다. 하나면 가운데."""
    if count <= 1:
        return np.zeros((count, N_COMPONENTS), dtype=np.float32)
    angles = np.arange(count) * (2 * math.pi / count)
    return np.stack([np.cos(angles), np.sin(angles)], axis=1).astype(np.float32) * COORD_LIMIT
