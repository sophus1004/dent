"""장치 목록 테스트: nvidia-smi 읽기, GPU 이름 줄이기, 이 PC 요약, GPU → CPU 순서."""

import subprocess
from typing import Any

import pytest

from dent.system import devices
from dent.system.devices import Device

NVIDIA_SMI_OUTPUT = (
    "0, NVIDIA GeForce RTX 4090, 24564, 20120\n1, NVIDIA GeForce RTX 4090, 24564, 23900\n"
)


def _runner(stdout: str) -> Any:
    """명령을 돌리지 않고 정한 출력을 주는 가짜."""

    def run(command: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    return run


def test_parse_nvidia_smi_reads_index_name_and_memory():
    # 실행
    found = devices.parse_nvidia_smi(NVIDIA_SMI_OUTPUT + "틀린 줄\n")

    # 확인: 번호 · 짧은 이름 · 메모리(MiB → 바이트), 모양이 틀린 줄은 건너뛴다
    assert [(device.key, device.label, device.name) for device in found] == [
        ("cuda:0", "GPU 0", "RTX 4090"),
        ("cuda:1", "GPU 1", "RTX 4090"),
    ]
    assert found[0].total_bytes == 24564 * 1024 * 1024
    assert found[1].free_bytes == 23900 * 1024 * 1024


def test_list_devices_puts_gpus_before_cpu(monkeypatch: pytest.MonkeyPatch):
    # 준비: NVIDIA GPU 2개인 Linux PC
    monkeypatch.setattr(devices.shutil, "which", lambda _name: "/usr/bin/nvidia-smi")
    monkeypatch.setattr(devices, "is_apple_silicon", lambda: False)

    # 실행
    found = devices.list_devices(run=_runner(NVIDIA_SMI_OUTPUT))

    # 확인
    assert [device.key for device in found] == ["cuda:0", "cuda:1", "cpu"]


def test_list_devices_has_only_cpu_without_gpu(monkeypatch: pytest.MonkeyPatch):
    # 준비
    monkeypatch.setattr(devices.shutil, "which", lambda _name: None)
    monkeypatch.setattr(devices, "is_apple_silicon", lambda: False)
    monkeypatch.setattr(devices, "ROCM_DEVICE_FILE", devices.Path("/없는/kfd"))

    # 실행
    found = devices.list_devices()

    # 확인: CPU는 늘 있다
    assert [device.key for device in found] == ["cpu"]
    assert found[0].name.endswith("코어")


def test_nvidia_gpus_is_empty_when_nvidia_smi_fails(monkeypatch: pytest.MonkeyPatch):
    # 준비: 드라이버가 멈췄다
    monkeypatch.setattr(devices.shutil, "which", lambda _name: "/usr/bin/nvidia-smi")

    def broken(command: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(command, 10)

    # 확인
    assert devices.nvidia_gpus(run=broken) == []


def test_short_name_drops_vendor():
    # 확인
    assert devices.short_name("NVIDIA GeForce RTX 4090") == "RTX 4090"
    assert devices.short_name("NVIDIA A100-SXM4-80GB") == "A100-SXM4-80GB"
    assert devices.short_name("Apple M4") == "Apple M4"


def test_machine_summary_counts_same_gpus(monkeypatch: pytest.MonkeyPatch):
    # 준비
    monkeypatch.setattr(devices.sys, "platform", "win32")
    gpus = [Device("cuda:0", "GPU 0", "RTX 4090"), Device("cuda:1", "GPU 1", "RTX 4090")]

    # 확인
    assert (
        devices.machine_summary([*gpus, Device("cpu", "CPU", "8코어")]) == "Windows · RTX 4090 ×2"
    )
    assert devices.machine_summary([Device("cpu", "CPU", "8코어")]) == "Windows · GPU 없음"
