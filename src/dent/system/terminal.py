"""실행 화면(터미널)의 모양: 세로 줄 단계 · 고르기 · 폴더 입력 · 진행 막대 · 로그 한 줄.

런처가 쓴다. 모양은 왼쪽 세로 줄에 단계가 하나씩 쌓이는 방식이다(◇ 끝난 단계 · ◆ 묻는 단계 · └ 끝).
한 번에 하나만 묻고, 고른 값은 한 줄로 접는다. 출력은 rich, 고르기 · 입력은 prompt_toolkit이 한다
(둘 다 Windows · Mac · Linux 터미널에서 돈다).

- 기호: UTF-8을 못 쓰거나 옛 Windows 콘솔이면 ◇ ● ━ 같은 기호 대신 o * = 같은 글자를 쓴다(fancy가 아니면).
- 색: 강조(보라) · 됨(초록) · 경고(노랑) · 오류(빨강) · 작업(청록) · 흐림. 터미널이 가진 색으로 rich가 맞춘다.
- 로그: 자식 프로세스(API 서버 · 작업 실행기 · 모델 서버)의 '[이름] 경고: 내용' 줄을 '시각  이름  내용'으로 바꿔 보인다.
"""

import os
import re
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from prompt_toolkit import PromptSession
from prompt_toolkit.application import Application
from prompt_toolkit.completion import PathCompleter
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.key_binding import KeyBindings, KeyPressEvent
from prompt_toolkit.layout import Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.styles import Style
from prompt_toolkit.utils import get_cwidth
from rich.console import Console, RenderableType
from rich.live import Live
from rich.progress import BarColumn, DownloadColumn, Progress, TaskProgressColumn, TextColumn
from rich.text import Text

# 강조 색 (DENT 보라). rich가 터미널이 가진 색 수에 맞춰 바꾼다.
ACCENT = "#8a74f5"

# 이름마다 로그의 색: 모델 서버는 강조, API · 작업 실행기는 청록
LABEL_STYLES = {"API": "cyan", "작업": "cyan", "임베딩": ACCENT, "Jev": ACCENT}

# 자식 프로세스의 로그 이름 → 짧은 이름
SHORT_LABELS = {"작업 실행기": "작업", "임베딩 모델": "임베딩", "Jev 모델": "Jev"}

# 로그 이름 칸의 폭 (한글 두 칸 기준으로 맞춘다)
LABEL_WIDTH = 8

# 자식 프로세스의 로그 한 줄: '[이름] 경고: 내용' · '[이름] 내용'
CHILD_LINE = re.compile(r"^\[(?P<label>[^\]]+)\] (?:(?P<level>경고|오류): )?(?P<message>.*)$")

# 기호: 고운 것(UTF-8)과 글자만 쓰는 것
FANCY_SYMBOLS = {
    "top": "┌",
    "rail": "│",
    "done": "◇",
    "ask": "◆",
    "end": "└",
    "on": "●",
    "off": "○",
    "fail": "■",
    "bar": "━",
    "spin": "◒◐◓◑",
}
PLAIN_SYMBOLS = {
    "top": "+",
    "rail": "|",
    "done": "o",
    "ask": "?",
    "end": "`",
    "on": "*",
    "off": "-",
    "fail": "x",
    "bar": "=",
    "spin": "|/-\\",
}

# 고르기 화면의 색 (prompt_toolkit)
PROMPT_STYLE = Style.from_dict(
    {
        "accent": f"{ACCENT} bold",
        "bold": "bold",
        "dim": "ansibrightblack",
        "chosen": "bold",
        "note": ACCENT,
    }
)

# Esc를 누른 뒤 다른 키가 이어지는지 기다리는 시간(초). 짧아야 Esc가 바로 먹는다.
ESCAPE_WAIT_S = 0.05

# 도는 줄에 보이는 마지막 글의 길이 (좁은 터미널에서 줄이 넘치지 않게)
STATUS_TEXT_LIMIT = 70


@dataclass(frozen=True)
class Option:
    """고를 것 하나."""

    # 보이는 이름. 예: 'GPU 0'
    label: str

    # 흐리게 보이는 값. 예: 'RTX 4090 · 여유 20.1 / 24.0 GB'
    detail: str = ""

    # 강조로 보이는 표. 예: '지난번'
    note: str = ""


class Terminal:
    """실행 화면을 그리는 곳. 런처가 하나만 만든다."""

    def __init__(self, console: Console | None = None) -> None:
        # 커서 자리 묻기(CPR)를 하지 않는다. 답하지 못하는 터미널에서 경고 줄이 찍히지 않게(작은 목록이라 없어도 된다).
        os.environ.setdefault("PROMPT_TOOLKIT_NO_CPR", "1")

        # 출력 (rich)
        self.console = console or Console(highlight=False, soft_wrap=True)

        # 기호와 색을 쓸 수 있는 터미널인지. 아니면 글자 기호만 쓴다.
        encoding = (self.console.encoding or "").lower()
        self.fancy = encoding.startswith("utf") and not self.console.legacy_windows

        # 기호
        self.symbols = FANCY_SYMBOLS if self.fancy else PLAIN_SYMBOLS

        # 사람이 고를 수 있는 터미널인지 (백그라운드 · 파이프면 묻지 않는다)
        self.interactive = self.console.is_terminal

    # ---------- 줄 ----------

    def header(self, version: str) -> None:
        """맨 위 한 줄: ┌  DENT 0.1.0"""
        line = Text.assemble((self.symbols["top"], "dim"), "   ", ("DENT", f"{ACCENT} bold"))
        line.append(f" {version}", style="dim")
        self.console.print(line)
        self.rail()

    def rail(self) -> None:
        """세로 줄만 있는 빈 줄."""
        self.console.print(Text(self.symbols["rail"], style="dim"))

    def done(self, title: str, detail: str = "", *, rail: bool = True) -> None:
        """끝난 단계 한 줄: ◇  제목 · 값"""
        line = Text.assemble((self.symbols["done"], "green"), "  ", title)
        if detail:
            line.append(f" · {detail}", style="dim")
        self.console.print(line)
        if rail:
            self.rail()

    def chosen(self, title: str, value: str, detail: str = "") -> None:
        """고른 값을 접은 한 줄: ◇  임베딩 · bge-m3 → GPU 1 · RTX 4090"""
        line = Text.assemble((self.symbols["done"], "green"), "  ", title, (" → ", "dim"), value)
        if detail:
            line.append(f" · {detail}", style="dim")
        self.console.print(line)
        self.rail()

    def note(self, message: str) -> None:
        """세로 줄 안의 흐린 한 줄: │  메시지"""
        self.console.print(Text.assemble((self.symbols["rail"], "dim"), "  ", (message, "dim")))

    def warn(self, message: str) -> None:
        """경고 한 줄: ◇  경고 · 메시지"""
        line = Text.assemble((self.symbols["done"], "yellow"), "  ", ("경고", "yellow bold"))
        line.append(f" · {message}")
        self.console.print(line)
        self.rail()

    def fail(self, message: str) -> None:
        """오류 한 줄: ■  오류 · 메시지 (실행하지 못하고 끝날 때)"""
        line = Text.assemble((self.symbols["fail"], "red"), "  ", ("오류", "red bold"))
        line.append(f" · {message}")
        self.console.print(line)

    def finish(self, title: str, url: str, detail: str) -> None:
        """마지막 줄: └  실행 중  http://…   종료 Ctrl+C"""
        line = Text.assemble((self.symbols["end"], "green"), "  ", (title, "bold"), "  ")
        line.append(url, style=f"{ACCENT} underline")
        line.append(f"   {detail}", style="dim")
        self.console.print(line)

    def end(self, message: str) -> None:
        """끝 줄(주소 없이): └  메시지"""
        self.console.print(Text.assemble((self.symbols["end"], "green"), "  ", (message, "bold")))

    def after(self, message: str) -> None:
        """마지막 줄 아래의 흐린 설명 한 줄."""
        self.console.print(Text(f"   {message}", style="dim"))

    def bar(self, fraction: float, *, width: int = 24) -> Text:
        """진행 막대 글자: ━━━━━━━━━━───── (채운 칸은 강조, 남은 칸은 흐림)."""
        filled = max(0, min(width, round(fraction * width)))
        return Text.assemble(
            (self.symbols["bar"] * filled, ACCENT),
            (self.symbols["bar"] * (width - filled), "dim"),
        )

    def spinner(self) -> str:
        """지금 시각의 도는 기호 한 글자."""
        frames = self.symbols["spin"]
        return frames[int(time.monotonic() * 4) % len(frames)]

    @contextmanager
    def progress(self, title: str, *, total: int) -> Iterator[Callable[[int], None]]:
        """세로 줄 안의 진행 막대(끝나면 지운다). 한 바이트 수를 넘기는 함수를 준다."""
        columns = [
            TextColumn(f"[dim]{self.symbols['rail']}[/dim]  {title}"),
            BarColumn(bar_width=24, style="dim", complete_style=ACCENT, finished_style=ACCENT),
            TaskProgressColumn(),
            DownloadColumn(binary_units=True),
        ]
        with Progress(*columns, console=self.console, transient=True) as progress:
            task = progress.add_task(title, total=total)
            yield lambda done: progress.update(task, completed=done)

    @contextmanager
    def status(self, title: str) -> Iterator[Callable[[str], None]]:
        """도는 기호 + 제목 + 마지막으로 받은 글 한 줄(끝나면 지운다). 글을 넘기는 함수를 준다."""
        latest = {"text": ""}

        def render() -> RenderableType:
            line = Text.assemble((self.spinner(), ACCENT), "  ", title)
            if latest["text"]:
                line.append(f"  {latest['text'][:STATUS_TEXT_LIMIT]}", style="dim")
            return line

        with Live(
            get_renderable=render, console=self.console, transient=True, refresh_per_second=8
        ):
            yield lambda text: latest.update(text=text)

    # ---------- 로그 ----------

    def event(self, label: str, message: str, *, level: str | None = None) -> None:
        """런처가 알리는 로그 한 줄: '시각  이름  (경고)  내용'. 자식 프로세스의 줄과 같은 모양이다."""
        self.console.print(self._log_text(label, message, level))

    def _log_text(self, label: str, message: str, level: str | None) -> Text:
        """'시각  이름  (경고|오류)  내용' 한 줄."""
        stamp = time.strftime("%H:%M:%S")
        padded = label + " " * max(1, LABEL_WIDTH - get_cwidth(label))
        text = Text.assemble((f"{stamp}  ", "dim"), (padded, LABEL_STYLES.get(label, "bold")))
        if level:
            text.append(f"{level}  ", style="yellow bold" if level == "경고" else "red bold")
        text.append(message)
        return text

    def child_line(self, line: str) -> Text:
        """자식 프로세스의 '[이름] 경고: 내용' 줄을 '시각  이름  경고  내용'으로. 모양이 다르면 흐리게."""
        matched = CHILD_LINE.match(line)
        if matched is None:
            return Text.assemble((f"{time.strftime('%H:%M:%S')}  ", "dim"), (line, "dim"))
        label = SHORT_LABELS.get(matched["label"], matched["label"])
        return self._log_text(label, matched["message"], matched["level"])

    @staticmethod
    def child_level(line: str) -> str | None:
        """자식 프로세스 줄의 수준: '경고' · '오류' · None(정보나 모르는 모양)."""
        matched = CHILD_LINE.match(line)
        return None if matched is None else matched["level"]

    # ---------- 묻기 ----------

    def select(self, title: str, options: list[Option], *, default: int, hint: str) -> int | None:
        """세로 줄 안의 목록에서 하나를 고른다. 고른 번호, Esc면 None. Ctrl+C면 KeyboardInterrupt.

        ◆  제목
        │  ● GPU 0   RTX 4090 · 여유 20.1 / 24.0 GB   지난번
        │  ○ CPU     12코어
        └  ↑↓ 고르기 · Enter 확인 · Esc …
        """
        state = {"index": default}
        label_width = max(get_cwidth(option.label) for option in options)
        keys = KeyBindings()

        @keys.add("up")
        @keys.add("k")
        def _up(_event: KeyPressEvent) -> None:
            state["index"] = (state["index"] - 1) % len(options)

        @keys.add("down")
        @keys.add("j")
        def _down(_event: KeyPressEvent) -> None:
            state["index"] = (state["index"] + 1) % len(options)

        @keys.add("enter")
        def _enter(event: KeyPressEvent) -> None:
            event.app.exit(result=state["index"])

        @keys.add("escape", eager=True)
        def _escape(event: KeyPressEvent) -> None:
            event.app.exit(result=None)

        @keys.add("c-c")
        def _interrupt(event: KeyPressEvent) -> None:
            event.app.exit(exception=KeyboardInterrupt())

        def render() -> StyleAndTextTuples:
            symbols = self.symbols
            parts: StyleAndTextTuples = [
                ("class:accent", symbols["ask"]),
                ("", "  "),
                ("class:bold", title),
                ("", "\n"),
            ]
            for index, option in enumerate(options):
                is_current = index == state["index"]
                gap = " " * (label_width - get_cwidth(option.label) + 3)
                parts += [
                    ("class:accent", symbols["rail"]),
                    ("", "  "),
                    (
                        "class:accent" if is_current else "class:dim",
                        symbols["on"] if is_current else symbols["off"],
                    ),
                    ("", " "),
                    ("class:chosen" if is_current else "", option.label),
                ]
                if option.detail:
                    parts += [("", gap), ("class:dim", option.detail)]
                if option.note:
                    parts += [("", "   "), ("class:note", option.note)]
                parts.append(("", "\n"))
            parts += [("class:accent", symbols["end"]), ("", "  "), ("class:dim", hint)]
            return parts

        app: Application[int | None] = Application(
            layout=Layout(Window(FormattedTextControl(render), always_hide_cursor=True)),
            key_bindings=keys,
            style=PROMPT_STYLE,
            erase_when_done=True,
        )
        app.ttimeoutlen = ESCAPE_WAIT_S
        return app.run()

    def ask_path(self, title: str, *, default: str, hint: str) -> str | None:
        """폴더 경로를 받는다(Tab 자동 완성). 입력한 경로, Esc면 None. Ctrl+C면 KeyboardInterrupt."""
        keys = KeyBindings()

        @keys.add("escape", eager=True)
        def _escape(event: KeyPressEvent) -> None:
            event.app.exit(result=None)

        message: StyleAndTextTuples = [
            ("class:accent", self.symbols["ask"]),
            ("", "  "),
            ("class:bold", title),
            ("", "\n"),
            ("class:accent", self.symbols["rail"]),
            ("", "  "),
        ]
        session: PromptSession[str] = PromptSession(
            completer=PathCompleter(expanduser=True, only_directories=True),
            complete_while_typing=True,
            key_bindings=keys,
            style=PROMPT_STYLE,
            erase_when_done=True,
            bottom_toolbar=[("class:dim", f"   {hint}")],
        )
        session.app.ttimeoutlen = ESCAPE_WAIT_S
        answer = session.prompt(message, default=default)
        return None if answer is None else answer.strip()


def bytes_text(size: int) -> str:
    """바이트를 GB(0.1 단위)로. 1GB보다 작으면 MB."""
    gigabytes = size / 1024**3
    if gigabytes >= 1:
        return f"{gigabytes:.1f} GB"
    return f"{size / 1024**2:.0f} MB"


def elapsed_text(seconds: float) -> str:
    """걸린 시간을 '1분 12초' · '8.4초'로."""
    if seconds < 60:
        return f"{seconds:.1f}초"
    minutes, rest = divmod(round(seconds), 60)
    return f"{minutes}분 {rest}초"


def minutes_text(seconds: int) -> str:
    """남은 시간을 '약 2분'으로 (1분 미만은 '1분 안')."""
    minutes = seconds // 60
    return "1분 안" if minutes < 1 else f"약 {minutes}분"


# 줄을 받아 처리하는 함수 (자식 프로세스 출력 읽기에 쓴다)
LineHandler = Callable[[str], None]
