import asyncio
import logging
import math
import sys
import time
import webbrowser
from contextlib import suppress
from dataclasses import dataclass
from logging.handlers import RotatingFileHandler

from prompt_toolkit import PromptSession
from prompt_toolkit.application import create_app_session, get_app
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.styles import Style
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from uninet_settings import COURSE_NAME, DATA_DIR


NAME = "Cuevio"
VERSION = "0.4.0"
REPOSITORY_URL = "https://github.com/RedWodoo/cuevio"
LOG_PATH = DATA_DIR / "logs" / "uninet.log"

_console = Console(highlight=False)
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
_CHAT_PROMPT = FormattedText([("class:prompt", "  cuevio › ")])
_CHAT_HINT = " pause/p  play/r  mute/m  sound/u  stop/q  help/h "
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
    return "█" * filled, "░" * (width - filled)


def show_queue(progress):
    filled, empty = queue_bar(progress, 20)
    content = Text.assemble(
        (filled, "bright_green"), (empty, "bright_magenta"), "\n",
        (f"{progress.completed}/{progress.total}", "bold"),
        f"  ·  Осталось {progress.total - progress.completed}  ·  {queue_estimate(progress)}",
    )
    _console.print(Panel(content, title=COURSE_NAME, subtitle="ОЧЕРЕДЬ", border_style="magenta", width=min(_console.width, 68)))


def playback_toolbar(progress, width):
    state = "Пауза" if progress.paused else progress.phase
    top = [("class:queue-label", " ОЧЕРЕДЬ  ")]
    if progress.total is None:
        top.append(("", state))
    else:
        filled, empty = queue_bar(progress, 12 if width >= 62 else 6)
        counts = f"  {progress.completed}/{progress.total} · {progress.total - progress.completed} ост."
        estimate = queue_estimate(progress)
        status = f" · {state}" if not progress.title else ""
        top.extend([
            ("class:queue-fill", filled),
            ("class:queue-track", empty),
            ("", counts),
        ])
        if sum(len(text) for _, text in top) + len(estimate + status) + 3 <= width:
            top.append(("class:queue-muted", f" · {estimate}{status}"))
        else:
            top.append(("class:queue-muted", f"\n  {estimate}{status}"))

    if progress.title:
        position = f"{int(progress.position) // 60}:{int(progress.position) % 60:02d}"
        duration = ""
        if progress.duration is not None:
            seconds = int(progress.duration)
            duration = f"/{seconds // 60}:{seconds % 60:02d}"
        current = Text(f"Week {progress.week} · {progress.title}")
        suffix = f"  {position}{duration}  ·  {state}"
        current.truncate(max(6, width - len(suffix) - 4), overflow="ellipsis")
        top.extend([
            ("class:queue-muted", f"\n ▸ {current.plain}"),
            ("class:queue-paused" if progress.paused else "", suffix),
        ])
    hints = _CHAT_HINT if width >= len(_CHAT_HINT) else " pause/p  play/r  stop/q  help/h "
    top.append(("class:queue-muted", "\n" + hints))
    return FormattedText(top)


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
    line = Text.assemble(
        (time.strftime("%H:%M:%S"), "dim"),
        "  ",
        (label.ljust(7), style),
        "  ",
        message,
    )
    _console.print(line, soft_wrap=True)
    _logger.log(level, "%s", message)


def open_support():
    url = f"{REPOSITORY_URL}#support"
    if not webbrowser.open(url):
        _console.print(Text(f"Поддержка · {url}"))
    return 0


def menu_choice(options):
    table = Table.grid(padding=(0, 2))
    for key, label in options:
        table.add_row(Text(key, style="bold bright_magenta"), Text(label))
    _console.print(table)
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
        _console.print(Text(
            "  pause/p  Пауза        play/r  Продолжить\n"
            "  mute/m   Без звука    sound/u Включить звук\n"
            "  stop/q   Завершить просмотр               Tab  Дополнение",
            style="bright_magenta",
        ))
    elif command:
        commands.put(command)
    else:
        log("Неизвестная команда. Введи help для подсказки.", kind="control")


async def _playback_chat(action, commands, session):
    # Keep terminal input on the main thread and all Playwright calls on one worker.
    async def read_command():
        try:
            return await session.prompt_async()
        except (EOFError, KeyboardInterrupt):
            return None

    with create_app_session(input=session.app.input, output=session.app.output), patch_stdout(raw=True):
        worker = asyncio.create_task(asyncio.to_thread(action))
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


def run_playback_chat(action, commands, progress):
    session = PromptSession(
        _CHAT_PROMPT,
        style=_CHAT_STYLE,
        completer=WordCompleter(list(_ALIASES), ignore_case=True),
        bottom_toolbar=lambda: playback_toolbar(progress(), get_app().output.get_size().columns),
        placeholder=FormattedText([("class:placeholder", "Напиши команду и нажми Enter")]),
        refresh_interval=0.5,
    )
    return asyncio.run(_playback_chat(action, commands, session))
