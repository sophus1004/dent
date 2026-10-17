"""되밀기 규칙 테스트: 429 · 503 + Retry-After(초 · HTTP 날짜)면 기다릴 시간, 아니면 None, 긴 값은 상한까지."""

from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import httpx

from dent.system import backoff


def _response(status_code: int, retry_after: str | None) -> httpx.Response:
    headers = {} if retry_after is None else {"Retry-After": retry_after}
    return httpx.Response(status_code, headers=headers)


def test_busy_delay_reads_seconds_and_adds_jitter():
    # 실행
    delay = backoff.busy_delay(_response(503, "4"))

    # 확인: 4초에 0 ~ 25%를 더한다
    assert delay is not None and 4.0 <= delay <= 5.0


def test_busy_delay_reads_http_date():
    # 준비
    when = format_datetime(datetime.now(UTC) + timedelta(seconds=10), usegmt=True)

    # 실행
    delay = backoff.busy_delay(_response(429, when))

    # 확인
    assert delay is not None and 8.0 <= delay <= 12.5


def test_busy_delay_is_none_without_retry_after_or_for_other_status():
    # 실행 · 확인
    assert backoff.busy_delay(_response(503, None)) is None
    assert backoff.busy_delay(_response(500, "3")) is None
    assert backoff.busy_delay(_response(429, "soon")) is None


def test_busy_delay_caps_long_wait():
    # 실행
    delay = backoff.busy_delay(_response(503, "3600"))

    # 확인
    assert delay is not None
    assert backoff.MAX_DELAY_S <= delay <= backoff.MAX_DELAY_S * (1 + backoff.JITTER_FRACTION)
