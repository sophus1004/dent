"""이 PC에서 내장 모델을 올릴 수 있는 장치: NVIDIA GPU(번호마다) · Apple GPU · AMD GPU · CPU.

런처가 실행할 때 목록을 보이고 고르게 한다. PyTorch를 불러오지 않고 가볍게 읽는다(실행 시간이 늘지 않게):
- NVIDIA: nvidia-smi가 GPU 번호 · 이름 · 전체 · 여유 메모리를 준다(Windows · Linux).
- Apple GPU: Apple Silicon Mac이면 있다. 메모리는 CPU와 같이 쓴다.
- AMD(Linux ROCm): 드라이버(/dev/kfd)가 있을 때만 설치된 PyTorch에 물어본다(ROCm판 PyTorch는 cuda 장치로 보인다).
- CPU: 늘 있다. 코어 수와 메모리.
장치 이름(key)은 PyTorch의 장치 글자와 같다: cuda:0 · mps · cpu. 실제로 올릴 수 있는지는 모델 서버가 올릴 때 다시 본다.
"""

import json
import os
import platform
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import psutil

# nvidia-smi가 답하기를 기다리는 시간(초). 드라이버가 없거나 멈췄으면 GPU 없음으로 본다.
NVIDIA_SMI_TIMEOUT_S = 10.0

# PyTorch에 AMD GPU를 물을 때 기다리는 시간(초). PyTorch를 불러오는 데 몇 초 걸린다.
TORCH_QUERY_TIMEOUT_S = 30.0

# AMD GPU 드라이버(ROCm)가 있으면 생기는 장치 파일
ROCM_DEVICE_FILE = Path("/dev/kfd")

# 1MiB (nvidia-smi는 메모리를 MiB로 준다)
BYTES_PER_MIB = 1024 * 1024

# GPU 이름 앞의 회사 이름. 좁은 터미널에서 짧게 보이려고 뗀다.
VENDOR_PREFIXES = ("NVIDIA GeForce ", "NVIDIA ", "AMD Radeon ", "AMD ")

# 설치된 PyTorch에 GPU 목록을 묻는 코드 (따로 된 프로세스에서 돈다)
TORCH_QUERY = (
    "import json, torch\n"
    "found = []\n"
    "if torch.cuda.is_available():\n"
    "    for i in range(torch.cuda.device_count()):\n"
    "        free, total = torch.cuda.mem_get_info(i)\n"
    "        found.append([i, torch.cuda.get_device_name(i), total, free])\n"
    "print(json.dumps(found))\n"
)

# 명령을 돌리는 함수 (테스트가 가짜로 바꾼다)
Runner = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class Device:
    """모델을 올릴 수 있는 장치 하나."""

    # PyTorch 장치 글자: cuda:0 · mps · cpu
    key: str

    # 사람이 읽는 이름: 'GPU 0' · 'Apple GPU' · 'CPU'
    label: str

    # 자세한 이름: 'RTX 4090' · 'Apple M4' · '12코어'. 모르면 ''
    name: str

    # 전체 · 여유 메모리(바이트). 모르면 None. Apple GPU는 CPU와 같이 쓰는 메모리다.
    total_bytes: int | None = None
    free_bytes: int | None = None

    @property
    def is_gpu(self) -> bool:
        """GPU인지 (CPU가 아니면 GPU)."""
        return self.key != "cpu"


def list_devices(*, run: Runner = subprocess.run) -> list[Device]:
    """이 PC의 장치: GPU(NVIDIA → AMD → Apple) 다음에 CPU. 늘 CPU 하나는 있다."""
    gpus = nvidia_gpus(run=run)
    if not gpus and ROCM_DEVICE_FILE.exists():
        gpus = torch_gpus(run=run)
    if is_apple_silicon():
        gpus.append(apple_gpu())
    return [*gpus, cpu_device()]


def nvidia_gpus(*, run: Runner = subprocess.run) -> list[Device]:
    """nvidia-smi가 알려 주는 NVIDIA GPU. nvidia-smi가 없거나 실패하면 빈 목록."""
    command = shutil.which("nvidia-smi")
    if command is None:
        return []
    try:
        result = run(
            [
                command,
                "--query-gpu=index,name,memory.total,memory.free",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=NVIDIA_SMI_TIMEOUT_S,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    return parse_nvidia_smi(result.stdout)


def parse_nvidia_smi(output: str) -> list[Device]:
    """nvidia-smi CSV('0, NVIDIA GeForce RTX 4090, 24564, 20120')를 장치로. 모양이 틀린 줄은 건너뛴다."""
    found: list[Device] = []
    for line in output.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) != 4:
            continue
        index, name, total, free = parts
        try:
            found.append(
                Device(
                    key=f"cuda:{int(index)}",
                    label=f"GPU {int(index)}",
                    name=short_name(name),
                    total_bytes=int(float(total)) * BYTES_PER_MIB,
                    free_bytes=int(float(free)) * BYTES_PER_MIB,
                )
            )
        except ValueError:
            continue
    return found


def torch_gpus(*, run: Runner = subprocess.run) -> list[Device]:
    """설치된 PyTorch가 보는 GPU(AMD ROCm판 포함). PyTorch가 없거나 실패하면 빈 목록."""
    try:
        result = run(
            [sys.executable, "-c", TORCH_QUERY],
            capture_output=True,
            text=True,
            timeout=TORCH_QUERY_TIMEOUT_S,
            check=True,
        )
        rows = json.loads(result.stdout.strip().splitlines()[-1])
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return []
    return [
        Device(
            key=f"cuda:{index}",
            label=f"GPU {index}",
            name=short_name(str(name)),
            total_bytes=int(total),
            free_bytes=int(free),
        )
        for index, name, total, free in rows
    ]


def is_apple_silicon() -> bool:
    """Apple Silicon Mac인지 (Apple GPU를 쓸 수 있는지)."""
    return sys.platform == "darwin" and platform.machine() == "arm64"


def apple_gpu() -> Device:
    """Apple GPU. 메모리는 CPU와 같이 쓰므로 PC 전체 메모리를 보인다."""
    memory = psutil.virtual_memory()
    return Device(
        key="mps",
        label="Apple GPU",
        name=_apple_chip_name(),
        total_bytes=memory.total,
        free_bytes=memory.available,
    )


def cpu_device() -> Device:
    """CPU. 이름 자리에 코어 수를 둔다."""
    memory = psutil.virtual_memory()
    cores = os.cpu_count() or 1
    return Device(
        key="cpu",
        label="CPU",
        name=f"{cores}코어",
        total_bytes=memory.total,
        free_bytes=memory.available,
    )


def short_name(name: str) -> str:
    """GPU 이름에서 회사 이름을 뗀다: 'NVIDIA GeForce RTX 4090' → 'RTX 4090'."""
    for prefix in VENDOR_PREFIXES:
        if name.startswith(prefix):
            return name[len(prefix) :]
    return name


def machine_summary(devices: list[Device]) -> str:
    """이 PC를 한 줄로: 'macOS · Apple GPU' · 'Windows · RTX 4090 ×2' · 'Linux · GPU 없음'."""
    system = {"darwin": "macOS", "win32": "Windows"}.get(sys.platform, "Linux")
    gpus = [device for device in devices if device.is_gpu]
    if not gpus:
        return f"{system} · GPU 없음"
    names = [device.name or device.label for device in gpus]
    first = names[0]
    same = all(name == first for name in names)
    gpu_text = f"{first} ×{len(names)}" if same and len(names) > 1 else " · ".join(names)
    return f"{system} · {gpu_text}"


def _apple_chip_name() -> str:
    """Mac의 칩 이름(예: 'Apple M4'). 모르면 ''."""
    try:
        result = subprocess.run(
            ["sysctl", "-n", "machdep.cpu.brand_string"],
            capture_output=True,
            text=True,
            timeout=NVIDIA_SMI_TIMEOUT_S,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip()
