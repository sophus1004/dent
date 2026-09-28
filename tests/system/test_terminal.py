"""실행 화면 테스트: 단계 줄 · 글자 기호로 바꾸기, 자식 로그 줄 바꾸기, 화살표 고르기 · Esc · 폴더 입력(키를 흘려 넣음)."""

import io

import pytest
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from rich.console import Console

from dent.system import terminal
from dent.system.terminal import Option, Terminal

OPTIONS = [Option("GPU 0", "RTX 4090"), Option("CPU", "8코어"), Option("올리지 않음")]


class _Screen(io.StringIO):
    """인코딩을 정할 수 있는 가짜 터미널 출력."""

    def __init__(self, encoding: str) -> None:
        super().__init__()
        self._name = encoding

    @property
    def encoding(self) -> str:  # type: ignore[override]
        return self._name


def _terminal(*, encoding: str = "utf-8") -> tuple[Terminal, io.StringIO]:
    """출력을 글로 받는 실행 화면. encoding이 UTF-8이 아니면 글자 기호를 쓴다."""
    buffer = _Screen(encoding)
    console = Console(file=buffer, width=100, color_system=None, legacy_windows=False)
    return Terminal(console), buffer


def _keys(text: str, action):  # type: ignore[no-untyped-def]
    """키를 흘려 넣고 action(고르기 · 입력)을 돌린다."""
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        pipe.send_text(text)
        return action()


def test_steps_use_fancy_symbols_on_utf8():
    # 준비
    term, buffer = _terminal()

    # 실행
    term.header("0.1.0")
    term.chosen("임베딩 · bge-m3", "GPU 1", "RTX 4090")

    # 확인
    assert buffer.getvalue().splitlines() == [
        "┌   DENT 0.1.0",
        "│",
        "◇  임베딩 · bge-m3 → GPU 1 · RTX 4090",
        "│",
    ]


def test_steps_fall_back_to_plain_symbols_without_utf8():
    # 준비: 옛 콘솔처럼 UTF-8이 아니다
    term, buffer = _terminal(encoding="cp949")

    # 실행
    term.done("설정", "포트 8000")

    # 확인
    assert not term.fancy
    assert buffer.getvalue().splitlines() == ["o  설정 · 포트 8000", "|"]


def test_child_line_turns_label_and_level_into_columns():
    # 준비
    term, _buffer = _terminal()

    # 실행
    warned = term.child_line("[작업 실행기] 경고: 임베딩 요청 다시 보냄")
    info = term.child_line("[API] 확인을 마쳤습니다.")
    raw = term.child_line("Traceback (most recent call last):")

    # 확인: 시각 뒤에 짧은 이름 · 수준 · 내용, 모양이 다른 줄은 그대로
    assert warned.plain[10:] == "작업    경고  임베딩 요청 다시 보냄"
    assert info.plain[10:] == "API     확인을 마쳤습니다."
    assert raw.plain.endswith("Traceback (most recent call last):")
    assert Terminal.child_level("[API] 오류: 멈춤") == "오류"
    assert Terminal.child_level("[API] 실행 중") is None


def test_select_moves_with_arrow_and_confirms_with_enter():
    # 준비
    term, _buffer = _terminal()

    # 실행: ↓ 한 번, Enter
    chosen = _keys("\x1b[B\r", lambda: term.select("임베딩", OPTIONS, default=0, hint=""))

    # 확인
    assert chosen == 1


def test_select_returns_none_on_escape():
    # 준비
    term, _buffer = _terminal()

    # 실행
    chosen = _keys("\x1b", lambda: term.select("임베딩", OPTIONS, default=2, hint=""))

    # 확인
    assert chosen is None


def test_select_raises_keyboard_interrupt_on_ctrl_c():
    # 준비
    term, _buffer = _terminal()

    # 실행 · 확인
    with pytest.raises(KeyboardInterrupt):
        _keys("\x03", lambda: term.select("임베딩", OPTIONS, default=0, hint=""))


def test_ask_path_returns_typed_folder():
    # 준비
    term, _buffer = _terminal()

    # 실행: 처음 값 뒤에 이어 친다
    answer = _keys("/model\r", lambda: term.ask_path("폴더", default="/home/jw", hint=""))

    # 확인
    assert answer == "/home/jw/model"


def test_texts_for_sizes_and_times():
    # 확인
    assert terminal.bytes_text(2 * 1024**3 + 100 * 1024**2) == "2.1 GB"
    assert terminal.bytes_text(5 * 1024**2) == "5 MB"
    assert terminal.elapsed_text(8.44) == "8.4초"
    assert terminal.elapsed_text(72) == "1분 12초"
    assert terminal.minutes_text(130) == "약 2분"
    assert terminal.minutes_text(30) == "1분 안"
