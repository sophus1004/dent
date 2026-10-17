"""외부 서버가 '바쁘니 나중에 다시'라고 할 때 기다렸다 다시 보내는 규칙. 임베딩 · Jev · LLM이 같이 쓴다.

서버가 429 · 503에 Retry-After를 실어 보내면 바쁜 것이다(살아 있고, 그만큼 뒤에 다시 오라는 뜻). 내장 모델 서버는
기다리는 요청이 많으면 이렇게 되민다. 그 시간에 무작위 흔들기를 더해 기다렸다 다시 보낸다: 작업 실행기 여럿이
같은 서버에 함께 다시 보내 다시 몰리지 않게. 기다린 시간이 부른 쪽의 한도를 넘으면 그만둔다(실패로 알린다).
Retry-After가 없는 실패는 클라이언트마다의 원래 규칙(몇 번 다시 보내기 · 바로 실패)을 따른다.
"""

import random
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx

# 바쁘다고 알리는 상태 코드: 요청이 너무 많음 · 잠깐 쓸 수 없음
BUSY_STATUSES = (429, 503)

# 한 번에 기다리는 최대 시간(초). 서버가 더 길게 말해도 이만큼 기다리고 다시 물어본다.
MAX_DELAY_S = 30.0

# 기다리는 시간에 더하는 무작위 몫의 상한 (0.25 = 0 ~ 25% 더)
JITTER_FRACTION = 0.25

RETRY_AFTER_HEADER = "retry-after"


def busy_delay(response: httpx.Response) -> float | None:
    """서버가 바쁘다고 알렸으면 기다릴 시간(초, 흔들기 포함), 아니면 None."""
    if response.status_code not in BUSY_STATUSES:
        return None
    value = response.headers.get(RETRY_AFTER_HEADER)
    if value is None:
        return None
    seconds = _seconds_of(value.strip())
    if seconds is None:
        return None
    bounded = min(max(seconds, 0.0), MAX_DELAY_S)
    return bounded * (1 + random.uniform(0, JITTER_FRACTION))


def _seconds_of(value: str) -> float | None:
    """Retry-After 값(초 또는 HTTP 날짜)을 초로. 읽지 못하면 None."""
    try:
        return float(value)
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return (when - datetime.now(UTC)).total_seconds()
