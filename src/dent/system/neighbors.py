"""가까운 벡터 찾기: 벡터마다 코사인 유사도가 가장 큰 이웃 k개(자기 자신은 뺀다). 모듈 종류와 상관없이 쓴다.

뜻 분석(근접 중복 · 오라벨 의심)의 바탕이다. 정확한 계산을 묶음으로 나눠 한다:
벡터 묶음 × 전체 벡터의 행렬 곱으로 유사도를 구하고, 줄마다 위 k개만 남긴다.
문장 12만 7천 건 × 1024차원 · 이웃 10개면 이 PC(M4)에서 약 2분이다(2026-09-25 측정).
그보다 훨씬 크면 가까운 벡터 찾기 인덱스(ANN)가 필요하다.
벡터는 float32로 받고 길이를 1로 맞춰 쓴다(코사인 유사도 = 내적). 계산이 길어 부르는 쪽이 스레드에서 돌린다.
"""

from dataclasses import dataclass

import numpy as np

# 한 번에 유사도를 계산하는 줄 수. 512 × 20만 × 4바이트 ≈ 400MB를 넘지 않게 한다.
NEIGHBOR_BLOCK_ROWS = 512


@dataclass(frozen=True)
class Neighbors:
    """벡터마다 가까운 이웃. 줄 i는 벡터 i의 이웃이고, 가까운 것부터 놓는다."""

    # 이웃 번호 (n, k)
    indices: np.ndarray

    # 이웃과의 코사인 유사도 (n, k), 큰 것부터
    similarities: np.ndarray


def nearest_neighbors(vectors: np.ndarray, *, k: int) -> Neighbors:
    """벡터마다 코사인 유사도가 가장 큰 이웃 k개. 벡터가 k개 이하면 있는 만큼(자기 자신 빼고)."""
    count = len(vectors)
    k = min(k, max(count - 1, 0))
    if k == 0:
        empty = np.zeros((count, 0))
        return Neighbors(indices=empty.astype(np.int64), similarities=empty.astype(np.float32))
    unit = _normalized(vectors)
    indices = np.empty((count, k), dtype=np.int64)
    similarities = np.empty((count, k), dtype=np.float32)
    for start in range(0, count, NEIGHBOR_BLOCK_ROWS):
        end = min(start + NEIGHBOR_BLOCK_ROWS, count)
        block = unit[start:end] @ unit.T
        # 자기 자신은 이웃이 아니다.
        block[np.arange(end - start), np.arange(start, end)] = -np.inf
        # 위 k개만 고른 뒤(순서 없음) 그 안에서 큰 것부터 줄 세운다. 전체 정렬보다 훨씬 빠르다.
        top = np.argpartition(-block, k - 1, axis=1)[:, :k]
        top_similarities = np.take_along_axis(block, top, axis=1)
        order = np.argsort(-top_similarities, axis=1, kind="stable")
        indices[start:end] = np.take_along_axis(top, order, axis=1)
        similarities[start:end] = np.take_along_axis(top_similarities, order, axis=1)
    return Neighbors(indices=indices, similarities=similarities)


def _normalized(vectors: np.ndarray) -> np.ndarray:
    """길이를 1로 맞춘 float32 벡터. 길이 0인 벡터는 그대로 0으로 둔다."""
    unit = np.asarray(vectors, dtype=np.float32)
    norms = np.linalg.norm(unit, axis=1, keepdims=True)
    return unit / np.where(norms == 0, 1, norms)
