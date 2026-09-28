"""가까운 벡터 찾기 테스트: 이웃 순서, 자기 자신 빼기, 묶음 경계, 적은 벡터."""

import numpy as np

from dent.system import neighbors


def test_nearest_neighbors_orders_by_cosine_and_skips_self():
    # 준비: 0과 1은 거의 같은 방향, 2는 반대쪽
    vectors = np.array([[1.0, 0.0], [0.9, 0.1], [-1.0, 0.0]], dtype=np.float32)

    # 실행
    found = neighbors.nearest_neighbors(vectors, k=2)

    # 확인
    assert found.indices.tolist() == [[1, 2], [0, 2], [1, 0]]
    assert found.similarities[0, 0] > 0.99
    assert found.similarities[0, 1] == np.float32(-1.0)


def test_nearest_neighbors_gives_same_result_across_blocks(monkeypatch):
    # 준비: 묶음을 3줄로 줄여 여러 묶음으로 나눠도 한 번에 계산한 것과 같은지 본다.
    rng = np.random.default_rng(0)
    vectors = rng.normal(size=(20, 8)).astype(np.float32)
    whole = neighbors.nearest_neighbors(vectors, k=4)
    monkeypatch.setattr(neighbors, "NEIGHBOR_BLOCK_ROWS", 3)

    # 실행
    blocked = neighbors.nearest_neighbors(vectors, k=4)

    # 확인
    assert blocked.indices.tolist() == whole.indices.tolist()
    assert np.allclose(blocked.similarities, whole.similarities)


def test_nearest_neighbors_returns_what_there_is_when_vectors_are_few():
    # 실행
    one = neighbors.nearest_neighbors(np.ones((1, 4), dtype=np.float32), k=5)
    two = neighbors.nearest_neighbors(np.eye(2, dtype=np.float32), k=5)

    # 확인
    assert one.indices.shape == (1, 0)
    assert two.indices.tolist() == [[1], [0]]
