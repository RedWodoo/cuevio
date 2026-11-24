import asyncio
import logging
import math
import sys
import time
import webbrowser
from contextlib import suppress
from contextvars import ContextVar
from dataclasses import dataclass
from io import StringIO
from logging.handlers import RotatingFileHandler

from prompt_toolkit import PromptSession
from prompt_toolkit.application import create_app_session, get_app, in_terminal
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.formatted_text import ANSI, FormattedText
from prompt_toolkit.renderer import print_formatted_text
from prompt_toolkit.styles import Style
from rich.cells import cell_len
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from uninet_settings import COURSE_NAME, DATA_DIR


NAME = "Cuevio"
VERSION = "0.4.1"
REPOSITORY_URL = "https://github.com/RedWodoo/cuevio"
LOG_PATH = DATA_DIR / "logs" / "uninet.log"

_console = Console(highlight=False)
_playback_output = ContextVar("playback_output", default=None)
_logger = logging.getLogger("uninet-queue")
_logger.propagate = False
_logger.addHandler(logging.NullHandler())

_WORDMARK = (
    "█▀▀   █ █   █▀▀   █ █   ▀█▀   █▀█",
    "█     █ █   █▀    ▀▄▀    █    █ █",
    "▀▀▀   ▀▀▀   ▀▀▀    ▀    ▀▀▀   ▀▀▀",
)
_MODES = {
    "menu": ("ГЛАВНОЕ МЕНЮ", f"Очередь видеолекций KBTU UniNet  ·  {COURSE_NAME}"),
    "setup": ("НАСТРОЙКА", "Сохрани данные для входа в KBTU UniNet."),
    "check": ("ПРОВЕРКА", "Проверь, какие ролики UniNet уже засчитал."),
    "play": ("ПРОСМОТР", f"{COURSE_NAME}  /  По порядку  /  1×"),
}
_COMMANDS = {
    "pause": ("pause", "p"),
    "play": ("play", "r", "resume"),
    "mute": ("mute", "m"),
    "sound": ("sound", "u"),
    "stop": ("stop", "q", "quit"),
    "help": ("help", "h", "?"),
}
_ALIASES = {alias: command for command, aliases in _COMMANDS.items() for alias in aliases}
_CHAT_STYLE = Style.from_dict({
    "prompt": "bold #e1a5ff",
    "placeholder": "#9a8aa9",
    "bottom-toolbar": "noreverse bg:#242033 #e1d7ee",
    "queue-label": "bold #eda7ff",
    "queue-fill": "#aee9d2",
    "queue-track": "#665879",
    "queue-muted": "#b0a0c0",
    "queue-paused": "bold #ffd290",
})
_CHAT_PROMPT = FormattedText([("class:prompt", "cuevio › ")])
_CHAT_HINT = " pause/p  play/r  mute/m  sound/u  stop/q  help/h"
_EVENTS = {
    "info": ("СИСТЕМА", "bright_cyan", logging.INFO),
    "video": ("ВИДЕО", "bright_blue", logging.INFO),
    "done": ("ГОТОВО", "bright_green", logging.INFO),
    "retry": ("ПОВТОР", "yellow", logging.WARNING),
    "control": ("ПУЛЬТ", "bright_magenta", logging.INFO),
    "error": ("СБОЙ", "bright_red", logging.ERROR),
}


@dataclass(frozen=True)
class QueueProgress:
    phase: str = "Подключение"
    total: int | None = None
    completed: int = 0
    upcoming_seconds: float = 0
    upcoming_unknown: int = 0
    week: int | None = None
    title: str = ""
    position: float = 0
    duration: float | None = None
    paused: bool = False


def queue_estimate(progress):
    seconds = progress.upcoming_seconds
    unknown = progress.upcoming_unknown
    if progress.title:
        if progress.duration is None:
            unknown += 1
        else:
            seconds += max(0, progress.duration - progress.position)
    if unknown and not seconds:
        return "время уточняется"
    minutes = math.ceil(seconds / 60)
    hours, minutes = divmod(minutes, 60)
    time_left = f"{hours} ч {minutes:02d} мин" if hours else f"{minutes} мин"
    return f"от {time_left}" if unknown else f"≈ {time_left}"


def queue_bar(progress, width):
    filled = progress.completed * width // progress.total if progress.total else width
    return "━" * filled, "·" * (width - filled)


def show_queue(progress):
    if _playback_output.get() is not None:
        return
    filled, empty = queue_bar(progress, 20)
    content = Text.assemble(
        (filled, "bright_green"), (empty, "bright_magenta"), "\n",
        (f"{progress.completed}/{progress.total}", "bold"),
        f"  ·  Осталось {progress.total - progress.completed}  ·  {queue_estimate(progress)}",
    )
    _console.print(Panel(
        content, title=f"Очередь · {COURSE_NAME}", title_align="left",
        border_style="magenta", width=min(_console.width, 68),
    ))


def playback_toolbar(progress, width):
    # Leave the last cell blank to avoid the terminal wrapping at the right edge.
    width = max(1, width - 1)
    state = "Пауза" if progress.paused else progress.phase
    state_style = "class:queue-paused" if progress.paused else ""
    summary = [("class:queue-label", " ОЧЕРЕДЬ  ")]
    estimate = ""
    if progress.total is None:
        summary.append((state_style, state))
    else:
        counts = f"{progress.completed}/{progress.total}"
        bar_width = min(12, max(0, width - cell_len(counts) - 15))
        filled, empty = queue_bar(progress, bar_width)
        summary.extend([
            ("class:queue-fill", filled), ("class:queue-track", empty),
            ("", f"  {counts}" if bar_width else counts),
        ])
        estimate = f"Осталось {progress.total - progress.completed} · {queue_estimate(progress)}"
    narrow = width < 66
    if estimate and not narrow:
        summary.append(("class:queue-muted", f"  ·  {estimate}"))

    media = [(state_style, f" {state}" if progress.total is not None else "")]
    title = []
    if progress.title:
        minutes, seconds = divmod(int(progress.position), 60)
        clock = f"{minutes}:{seconds:02d}"
        if progress.duration is not None:
            minutes, seconds = divmod(int(progress.duration), 60)
            clock += f"/{minutes}:{seconds:02d}"
        if not narrow:
            media = [(state_style, f" {state:<10}")]
        media.append(("", f"  {clock}" if narrow else f"  {clock:<10}"))
        title = [("class:queue-muted", f"Week {progress.week} · {progress.title}")]
        if not narrow:
            media.extend([("class:queue-muted", "  ·  "), *title])

    lines = [summary]
    if narrow:
        lines.append([("class:queue-muted", f" {estimate}" if estimate else "")])
    lines.append(media)
    if narrow:
        lines.append([("", " "), *title])
    hints = _CHAT_HINT if width >= len(_CHAT_HINT) else " p пауза · r пуск · q стоп · h помощь"
    lines.append([("class:queue-muted", hints)])

    toolbar = FormattedText()
    for index, line in enumerate(lines):
        if index:
            toolbar.append(("", "\n"))
        remaining = width
        for style, value in line:
            text = Text(value)
            text.truncate(remaining, overflow="ellipsis")
            toolbar.append((style, text.plain))
            remaining -= text.cell_len
            if not remaining:
                break
    return toolbar


def show_banner(mode):
    label, description = _MODES[mode]
    wordmark = Text()
    if _console.width >= 45:
        for line, color in zip(_WORDMARK, ("bright_magenta", "magenta", "blue")):
            wordmark.append(f"{line}\n", style=color)
        wordmark.append("V I D E O   Q U E U E", style="bold bright_white")
    else:
        wordmark.append(NAME, style="bold bright_cyan")

    details = Text(description)
    if mode == "setup":
        details.append(
            "\n\nПароль хранится на этом компьютере в Диспетчере учётных данных Windows."
            "\nПри входе Chrome передаёт его по HTTPS только официальной форме KBTU.",
        )
    _console.print(Panel(
        Group(wordmark, Text(), details),
        title=f"{NAME}  /  v{VERSION}",
        title_align="left",
        subtitle=label,
        subtitle_align="right",
        border_style="bright_magenta",
        padding=(1, 2),
        width=min(_console.width, 80),
    ))
    support = Text("  Поддержка · ", style="dim")
    support.append("GitHub", style=f"underline cyan link {REPOSITORY_URL}#support")
    if not _console.is_terminal or _console.legacy_windows:
        support.append(f"  {REPOSITORY_URL}#support", style="dim")
    _console.print(support)
    _console.print()


def configure_log(mode):
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(LOG_PATH, maxBytes=512_000, backupCount=2, encoding="utf-8")
    except OSError:
        log("Локальный журнал недоступен. Сообщения остаются в консоли.", kind="retry")
        return
    handler.setFormatter(logging.Formatter(
        "%(asctime)s  %(levelname)-7s  %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    ))
    _logger.addHandler(handler)
    _logger.setLevel(logging.INFO)
    _logger.info("%s v%s / %s", NAME, VERSION, mode)


def log(message, *, kind="info"):
    label, style, level = _EVENTS[kind]
    line = Table.grid(padding=(0, 2), collapse_padding=True)
    line.add_column(width=8, no_wrap=True)
    line.add_column(width=7, no_wrap=True)
    line.add_column(overflow="fold")
    line.add_row(
        Text(time.strftime("%H:%M:%S"), style="dim"),
        Text(label, style=style),
        Text(message, end=""),
    )
    print_message(line)
    _logger.log(level, "%s", message)


def print_message(message):
    emit = _playback_output.get()
    if emit is None:
        _console.print(message)
    else:
        emit(message)


def open_support():
    url = f"{REPOSITORY_URL}#support"
    if not webbrowser.open(url):
        _console.print(Text(f"Поддержка · {url}"))
    return 0


def print_options(options):
    table = Table.grid(padding=(0, 2), collapse_padding=True)
    table.add_column(width=12, no_wrap=True)
    table.add_column(overflow="fold")
    for key, label in options:
        table.add_row(Text(key, style="bold bright_magenta"), Text(label))
    print_message(table)


def menu_choice(options):
    print_options(options)
    words = [word.strip() for key, _ in options for word in key.split("/")]
    session = PromptSession(
        _CHAT_PROMPT, style=_CHAT_STYLE, completer=WordCompleter(words, ignore_case=True),
        placeholder=FormattedText([("class:placeholder", "Введи номер или команду")]),
    )
    return session.prompt().strip().lower()


def interactive_terminal():
    return sys.stdin.isatty() and sys.stdout.isatty()


def enqueue_command(text, commands):
    word = text.strip().lower()
    if not word:
        return
    command = _ALIASES.get(word)
    if command == "help":
        print_options((
            ("pause / p", "Пауза"),
            ("play / r", "Продолжить просмотр"),
            ("mute / m", "Выключить звук"),
            ("sound / u", "Включить звук"),
            ("stop / q", "Завершить просмотр"),
            ("Tab", "Дополнить команду"),
        ))
    elif command:
        commands.put(command)
    else:
        log("Неизвестная команда. Введи help для подсказки.", kind="control")


async def _print_playback_messages(messages, output):
    while (message := await messages.get()) is not None:
        batch = [message]
        while not messages.empty():
            message = messages.get_nowait()
            if message is None:
                break
            batch.append(message)

        # Rich formats the rows without touching the terminal. Prompt Toolkit
        # erases and restores the prompt and writes CRLF through its own output.
        buffer = StringIO()
        console = Console(
            file=buffer, width=max(1, output.get_size().columns - 1), highlight=False,
            force_terminal=True, color_system="standard", no_color=_console.no_color,
        )
        for row in batch:
            console.print(row)
        async with in_terminal():
            print_formatted_text(output, ANSI(buffer.getvalue()), _CHAT_STYLE)
        if message is None:
            return


async def _playback_chat(action, commands, session):
    # Keep terminal input on the main thread and all Playwright calls on one worker.
    async def read_command():
        try:
            return await session.prompt_async()
        except (EOFError, KeyboardInterrupt):
            return None

    output = session.app.output
    loop = asyncio.get_running_loop()
    messages = asyncio.Queue()
    token = _playback_output.set(lambda message: loop.call_soon_threadsafe(messages.put_nowait, message))
    try:
        with create_app_session(input=session.app.input, output=output):
            reporter = asyncio.create_task(_print_playback_messages(messages, output))
            worker = asyncio.create_task(asyncio.to_thread(action))
            try:
                while not worker.done():
                    prompt = asyncio.create_task(read_command())
                    finished, _ = await asyncio.wait((worker, prompt), return_when=asyncio.FIRST_COMPLETED)
                    if worker in finished:
                        prompt.cancel()
                        with suppress(asyncio.CancelledError):
                            await prompt
                        break
                    text = prompt.result()
                    if text is None:
                        commands.put("stop")
                        break
                    enqueue_command(text, commands)
                return await worker
            finally:
                messages.put_nowait(None)
                await reporter
    finally:
        _playback_output.reset(token)


def run_playback_chat(action, commands, progress):
    session = PromptSession(
        _CHAT_PROMPT,
        style=_CHAT_STYLE,
        completer=WordCompleter(list(_ALIASES), ignore_case=True),
        bottom_toolbar=lambda: playback_toolbar(progress(), get_app().output.get_size().columns),
        placeholder=FormattedText([("class:placeholder", "Напиши команду и нажми Enter")]),
        reserve_space_for_menu=0,
        refresh_interval=0.5,
    )
    return asyncio.run(_playback_chat(action, commands, session))
