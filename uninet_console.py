import asyncio
import logging
import sys
import time
import webbrowser
from contextlib import suppress
from logging.handlers import RotatingFileHandler

from prompt_toolkit import PromptSession
from prompt_toolkit.application import create_app_session
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
VERSION = "0.3.0"
REPOSITORY_URL = "https://github.com/RedWodoo/cuevio"
LOG_PATH = DATA_DIR / "logs" / "uninet.log"

_console = Console(highlight=False)
_logger = logging.getLogger("uninet-queue")
_logger.propagate = False
_logger.addHandler(logging.NullHandler())

_WORDMARK = (
    "█▀▀   █ █   █▀▀   █ █   ▀█▀   █▀█",
    "█     █ █   ██▄   ▀▄▀    █    █ █",
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
    "bottom-toolbar": "noreverse bg:#292236 #d9c7ed",
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


def run_playback_chat(action, commands):
    session = PromptSession(
        _CHAT_PROMPT,
        style=_CHAT_STYLE,
        completer=WordCompleter(list(_ALIASES), ignore_case=True),
        bottom_toolbar=_CHAT_HINT,
        placeholder=FormattedText([("class:placeholder", "Напиши команду и нажми Enter")]),
    )
    return asyncio.run(_playback_chat(action, commands, session))
