"""실행과 종료 확인: uv run python scripts/launch_check.py [--model]

`dent --no-browser -y`로 뒤에서 실행하고(명령은 실행된 뒤 끝나야 한다) /readyz가 준비되는지, `dent status`가 '실행 중'을 보이는지,
`dent stop`으로 관리 프로세스 · API 서버 · 작업 실행기 · 모델 서버 · 내장 DB가 모두 종료되는지 본다.
Windows · macOS · Linux에서 같은 명령으로 잰다. 여러 OS를 확인하는 CI가 쓴다. 되면 0, 안 되면 1로 끝나고 로그를 보인다.

--model: 내장 임베딩(bge-m3)을 CPU에 올려 본다. 모델 패키지를 이 PC에 맞는 판으로 설치하고(런처와 같은 명령),
지난번 선택(storage/models/choices.json)을 CPU로 적은 뒤 실행해서, 모델이 준비되면 임베딩 하나를 받아 본다.
처음이면 모델 파일(약 2.1GB)을 받는다.
"""

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from dent import supervisor
from dent.system import embedded_models
from dent.system.config import LOOPBACK_HOST, PROJECT_ROOT, Settings
from dent.system.embedded_models import ModelChoice
from dent.system.models import ConnectionRole

# 시험에 쓰는 화면 포트 (개발 중인 DENT와 부딪치지 않게 8000이 아닌 곳)
PORT = 8765

# 준비 · 모델 준비 · 종료를 기다리는 최대 시간(초). 모델은 처음이면 파일을 받느라 오래 걸린다.
READY_TIMEOUT_S = 180
MODEL_TIMEOUT_S = 1800
STOP_TIMEOUT_S = 120

# 모델 패키지 설치를 기다리는 최대 시간(초)
INSTALL_TIMEOUT_S = 1800

# 상태를 묻는 간격(초)
POLL_INTERVAL_S = 2.0

IS_WINDOWS = sys.platform == "win32"


def main() -> int:
    parser = argparse.ArgumentParser(description="DENT 실행과 종료 확인")
    parser.add_argument("--model", action="store_true", help="내장 임베딩을 CPU에 올려 본다")
    args = parser.parse_args()

    settings = Settings(port=PORT)
    if args.model:
        _install_model_packages()
        choice = ModelChoice(ConnectionRole.EMBEDDING, device="cpu")
        embedded_models.save_choices(
            settings, {ConnectionRole.EMBEDDING: choice, ConnectionRole.JEV: None}
        )

    started = _dent("--no-browser", "-y", timeout=READY_TIMEOUT_S)
    ok = started.returncode == 0 and _check(settings, model=args.model)
    status = _dent("status")
    ok = ok and "실행 중" in status.stdout
    stopped = _dent("stop", timeout=STOP_TIMEOUT_S)
    after = _dent("status")
    busy = [port for port in (PORT, PORT + 1, PORT + 2) if _is_port_in_use(port)]
    log = supervisor.log_path(settings)
    if log.exists():
        print("----- storage/logs/dent.log")
        print(log.read_text(encoding="utf-8", errors="replace"))
    print(f"stop 끝난 코드: {stopped.returncode} · 남은 포트: {busy or '없음'}")
    ok = ok and stopped.returncode == 0 and not busy and "실행 중이 아닙니다" in after.stdout
    print("결과:", "통과" if ok else "실패")
    return 0 if ok else 1


def _dent(*args: str, timeout: float = 60) -> subprocess.CompletedProcess[str]:
    """dent 명령 하나를 돌리고 출력을 보인다."""
    launcher = Path(sys.executable).parent / ("dent.exe" if IS_WINDOWS else "dent")
    env = dict(os.environ, PORT=str(PORT), PYTHONIOENCODING="utf-8")
    result = subprocess.run(
        [str(launcher), *args],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )
    print(f"$ dent {' '.join(args)}  → {result.returncode}")
    print(result.stdout + result.stderr)
    return result


def _check(settings: Settings, *, model: bool) -> bool:
    """준비되는지(모델을 올리면 모델 준비와 임베딩 하나까지) 본다."""
    body = _wait_ready()
    if body is None:
        print("준비되지 않았습니다")
        return False
    print("준비됨 · 작업 실행기:", body["info"]["worker"]["detail"])
    print(
        "모델 줄:", [(item["role"], item["state"], item.get("device")) for item in body["models"]]
    )
    if not model:
        return True
    port = embedded_models.model_port(settings, ConnectionRole.EMBEDDING)
    health = _wait_model(port)
    print("임베딩 모델:", health)
    if not health or health.get("status") != "ok":
        return False
    vector = _embed(port, "배송은 언제 오나요?")
    print("임베딩 차원:", len(vector))
    return len(vector) == 1024


def _wait_ready() -> dict | None:
    deadline = time.monotonic() + READY_TIMEOUT_S
    while time.monotonic() < deadline:
        body = _get_json(f"http://{LOOPBACK_HOST}:{PORT}/readyz")
        if body and body.get("ready") and body["info"]["worker"]["ok"]:
            return body
        time.sleep(POLL_INTERVAL_S)
    return None


def _wait_model(port: int) -> dict | None:
    deadline = time.monotonic() + MODEL_TIMEOUT_S
    health = None
    while time.monotonic() < deadline:
        health = _get_json(f"http://{LOOPBACK_HOST}:{port}/health")
        if health and health.get("status") in ("ok", "failed"):
            return health
        time.sleep(POLL_INTERVAL_S)
    return health


def _embed(port: int, text: str) -> list[float]:
    request = urllib.request.Request(
        f"http://{LOOPBACK_HOST}:{port}/v1/embeddings",
        data=json.dumps({"model": "BAAI/bge-m3", "input": [text]}).encode(),
        headers={"content-type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=300) as response:
        return json.load(response)["data"][0]["embedding"]


def _install_model_packages() -> None:
    """런처와 같은 명령으로 모델 패키지를 이 PC에 맞는 판으로 설치한다."""
    uv = os.environ.get("UV") or shutil.which("uv")
    if uv is None:
        raise SystemExit("uv를 찾지 못했습니다")
    with tempfile.TemporaryDirectory() as folder:
        constraints = Path(folder) / "constraints.txt"
        subprocess.run(
            embedded_models.export_command(uv, constraints), cwd=PROJECT_ROOT, check=True
        )
        subprocess.run(
            embedded_models.install_command(uv, sys.executable, constraints),
            cwd=PROJECT_ROOT,
            check=True,
            timeout=INSTALL_TIMEOUT_S,
        )


def _get_json(url: str) -> dict | None:
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            return json.load(response)
    except (urllib.error.URLError, OSError, ValueError):
        return None


def _is_port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1.0)
        return sock.connect_ex((LOOPBACK_HOST, port)) == 0


if __name__ == "__main__":
    sys.exit(main())
