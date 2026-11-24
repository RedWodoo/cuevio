import argparse
import ctypes
import getpass
import json
import re
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from queue import Empty, Queue
from urllib.parse import urljoin, urlsplit

from keyring.backends.Windows import WinVaultKeyring
from keyring.errors import KeyringError
from playwright.sync_api import Error as BrowserError
from playwright.sync_api import TimeoutError as BrowserTimeout
from playwright.sync_api import expect, sync_playwright

from uninet_console import (
    NAME,
    VERSION,
    configure_log,
    interactive_terminal,
    log,
    menu_choice,
    open_support,
    run_playback_chat,
    show_banner,
)
from uninet_settings import COURSE_CODE, COURSE_NAME, Preferences, load_preferences, save_preferences


PORTAL = "https://uninet.kbtu.kz"
SSO = "https://key.kbtu.kz"
MATERIALS_PATH = "/student/course-materials"
VAULT_SERVICE = "KBTU UniNet Physics I"
VAULT_ENTRY = "portal"
WEEK_NAME = re.compile(r"^Week\s+(\d+)\b", re.I)
STATUS = re.compile(r"^(?:Lecture completed|Watched \d+ of \d+ videos?)$", re.I)
COUNT = re.compile(r"Watched (\d+) of (\d+) videos?", re.I)
ROWS = "ol > li > button"
PLAYER = 'iframe[id^="__kinescope_player_"][src^="https://kinescope.io/embed/"]'
PLAY = re.compile(r"^(?:Смотреть|Смотреть видео|Play(?: video)?|Воспроизведение|Воспроизвести)(?: \(k\))?$", re.I)
PAUSE = re.compile(r"^(?:Пауза|Pause)(?: \(k\))?$", re.I)


class UniNetError(Exception):
    pass


class PlaybackError(UniNetError):
    pass


class PlaybackStopped(Exception):
    pass


@dataclass
class PlaybackControl:
    preferences: Preferences
    paused: bool = False
    commands: Queue[str] = field(default_factory=Queue)

    def poll(self):
        while True:
            try:
                command = self.commands.get_nowait()
            except Empty:
                return
            if command == "stop":
                raise PlaybackStopped
            if command in ("pause", "play"):
                pause = command == "pause"
                if self.paused != pause:
                    self.paused = pause
                    log("Пауза. Введи play, чтобы продолжить." if pause else "Продолжаю просмотр.", kind="control")
            elif command in ("mute", "sound"):
                muted = command == "mute"
                if self.preferences.muted != muted:
                    self.preferences = replace(self.preferences, muted=muted)
                    log("Звук выключен." if muted else "Звук включён.", kind="control")
                    try:
                        save_preferences(self.preferences)
                    except OSError:
                        log("Звук изменён для этого запуска. Сохранение недоступно.", kind="retry")

    def wait_until_running(self):
        self.poll()
        if not self.paused:
            return 0
        start = time.monotonic()
        while self.paused:
            time.sleep(0.2)
            self.poll()
        return time.monotonic() - start


@dataclass(frozen=True)
class Clip:
    title: str
    credited: bool
    resume_at: int | None


@dataclass(frozen=True)
class Lecture:
    completed: bool
    watched: int | None
    total: int | None
    clips: tuple[Clip, ...]

    def credited(self, index: int) -> bool:
        if self.completed:
            return True
        if self.clips:
            return self.clips[index].credited
        return self.watched == self.total == 1


def setup_credentials(vault):
    if not sys.stdin.isatty():
        raise UniNetError("Открой настройку в консоли Windows для скрытого ввода пароля.")
    username = ""
    while not username:
        username = input("Логин или почта KBTU: ").strip()
    password = ""
    while not password:
        if sys.version_info >= (3, 14):
            password = getpass.getpass("Пароль KBTU (звёздочки показывают ввод): ", echo_char="*")
        else:
            password = getpass.getpass("Пароль KBTU (символы при вводе скрыты): ")
    vault.set_password(
        VAULT_SERVICE,
        VAULT_ENTRY,
        json.dumps({"username": username, "password": password}),
    )
    log("Данные для входа сохранены в Диспетчере учётных данных Windows.", kind="done")


@contextmanager
def keep_awake():
    execution_state = ctypes.windll.kernel32.SetThreadExecutionState
    continuous = 0x80000000
    execution_state(continuous | 0x00000001 | 0x00000002)
    try:
        yield
    finally:
        execution_state(continuous)


def origin(url):
    parsed = urlsplit(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def at_student_portal(url):
    return origin(url) == PORTAL and urlsplit(url).path.startswith("/student")


def close_popup(scope):
    dismiss = re.compile(r"^(?:Close|Dismiss|Закрыть|Понятно)$", re.I)
    for role in ("dialog", "alertdialog"):
        close = scope.get_by_role(role).get_by_role("button", name=dismiss).first
        if close.is_visible():
            close.click(timeout=2400)
            return True
    cookie = scope.get_by_role(
        "button", name=re.compile(r"^(?:Reject optional cookies|Only necessary|Только необходимые)$", re.I)
    ).first
    if cookie.is_visible():
        cookie.click(timeout=2400)
        return True
    return False


def click(locator, scope, timeout=9000):
    try:
        locator.click(timeout=timeout)
    except BrowserTimeout:
        if not close_popup(scope):
            raise
        locator.click(timeout=timeout)


def sign_in(page, credentials):
    page.goto(f"{PORTAL}/login", wait_until="domcontentloaded")
    label = re.compile(r"^(?:Sign in with KBTU|Войти через КБТУ|Войти через KBTU)$", re.I)
    sign_in_button = page.get_by_role("button", name=label).or_(page.get_by_role("link", name=label))
    try:
        sign_in_button.wait_for(timeout=12000)
    except BrowserTimeout:
        if at_student_portal(page.url):
            return
        if origin(page.url) != SSO:
            raise UniNetError("На странице портала не появилась кнопка Sign in with KBTU.") from None
    else:
        click(sign_in_button, page)

    try:
        page.wait_for_url(
            lambda url: origin(url) == SSO or at_student_portal(url),
            wait_until="domcontentloaded",
            timeout=30000,
        )
        if at_student_portal(page.url):
            return
        form = page.locator("form").filter(has=page.locator("#password"))
        form.wait_for(timeout=15000)
        action = form.get_attribute("action")
        if origin(page.url) != SSO or not action or origin(urljoin(page.url, action)) != SSO:
            raise UniNetError("Адрес формы входа изменился. Проверь страницу входа KBTU.")
        form.locator("#username").fill(credentials["username"])
        form.locator("#password").fill(credentials["password"])
        form.locator("#kc-login").click()
        page.wait_for_url(at_student_portal, wait_until="domcontentloaded", timeout=45000)
    except BrowserError:
        raise UniNetError(
            "Вход в KBTU не завершился. Проверь пароль и сообщения на странице входа."
        ) from None


def week_picker(page):
    return page.get_by_role("combobox", name="Select week", exact=True)


def open_course(page, credentials):
    log("Вхожу в KBTU UniNet.")
    sign_in(page, credentials)
    log(f"Вход выполнен. Открываю {COURSE_NAME}.")
    materials = page.locator(f'nav a[href="{MATERIALS_PATH}"]').first
    try:
        click(materials, page, timeout=6000)
    except BrowserTimeout:
        page.goto(f"{PORTAL}{MATERIALS_PATH}", wait_until="domcontentloaded")
    course = page.get_by_role("button", name=re.compile(rf"\b{re.escape(COURSE_CODE)}\b"))
    click(course, page, timeout=20000)
    week_picker(page).wait_for(timeout=20000)


def show_weeks(page):
    picker = week_picker(page)
    if picker.get_attribute("aria-expanded") != "true":
        click(picker, page)
    options = page.get_by_role("option", name=WEEK_NAME)
    options.first.wait_for()
    return options


def list_weeks(page):
    weeks = [int(WEEK_NAME.match(name.strip())[1]) for name in show_weeks(page).all_inner_texts()]
    picker = week_picker(page)
    picker.press("Escape")
    expect(picker).to_have_attribute("aria-expanded", "false", timeout=3500)
    return list(dict.fromkeys(weeks))


def open_week(page, number):
    heading = page.get_by_role("heading", name=re.compile(rf"^Week {number}\s*:", re.I), level=2)
    if not heading.is_visible():
        show_weeks(page)
        option = page.get_by_role("option", name=re.compile(rf"^Week {number}\b", re.I))
        click(option, page)
    heading.wait_for(timeout=20000)


def lecture_cards(page):
    marker = page.get_by_text("Video lecture", exact=True)
    marker.first.wait_for(timeout=20000)
    cards = page.locator("main .glass-card").filter(has=marker)
    cards.first.wait_for()
    return cards.all()


def read_lecture(card):
    status = card.get_by_text(STATUS).first
    status.wait_for(timeout=12000)
    caption = status.inner_text().strip()
    count = COUNT.fullmatch(caption)
    watched, total = map(int, count.groups()) if count else (None, None)
    clips = tuple(Clip(**clip) for clip in card.locator(ROWS).evaluate_all(
        r"""rows => rows.map(button => {
            const resume = button.innerText.match(/\bresume at\s+((?:\d+:){1,2}\d{2})\b/i)?.[1];
            return {
                title: (button.querySelector('span.block.truncate')?.textContent || button.innerText).trim(),
                credited: !!button.querySelector('span:first-child svg.text-status-success'),
                resume_at: resume ? resume.split(':').reduce((seconds, part) => seconds * 60 + Number(part), 0) : null
            };
        })"""
    ))
    completed = caption.lower() == "lecture completed" or (total is not None and watched == total)
    return Lecture(completed, watched, total, clips)


def select_clip(card, index):
    row = card.locator(ROWS).nth(index)
    if row.get_attribute("aria-current") == "true":
        return
    iframe = card.locator(PLAYER).first
    old_id = iframe.get_attribute("id", timeout=1600) if iframe.count() else None
    click(row, card.page)
    expect(row).to_have_attribute("aria-current", "true", timeout=8000)
    if old_id:
        card.locator(f'iframe[id="{old_id}"]').wait_for(state="detached", timeout=8500)


def frame(card):
    return card.locator(PLAYER).first.content_frame


def media_state(player):
    video = player.locator("video").first
    try:
        return video.evaluate(
            """v => ({
                position: v.currentTime,
                duration: Number.isFinite(v.duration) ? v.duration : null,
                paused: v.paused,
                ended: v.ended,
                rate: v.playbackRate,
                muted: v.muted,
                error: v.error?.code ?? null
            })""",
            timeout=1100,
        )
    except BrowserError:
        return None


def sync_sound(video, state, control):
    if state and state["muted"] != control.preferences.muted:
        video.evaluate("(video, muted) => { video.muted = muted }", control.preferences.muted, timeout=2000)


def start_video(card, *, resume_at, control):
    player = frame(card)
    video = player.locator("video").first
    start = player.get_by_role("button", name=PLAY).first
    deadline = time.monotonic() + 16
    play_requested = False
    playback_seen_at = None
    while time.monotonic() < deadline:
        iteration_started = time.monotonic()
        control.poll()
        state = media_state(player)
        sync_sound(video, state, control)
        if control.paused:
            if state and not state["paused"]:
                video.evaluate("video => video.pause()", timeout=2000)
                play_requested = False
            time.sleep(0.25)
            deadline += time.monotonic() - iteration_started
            continue
        if state and state["position"] > (resume_at or 0) + 3:
            pause = player.get_by_role("button", name=PAUSE).first
            if not state["paused"] and pause.is_visible():
                try:
                    pause.click(timeout=1500)
                except BrowserTimeout:
                    pass
            raise UniNetError("Позиция плеера не совпала с сохранённым прогрессом UniNet.")
        if state and state["ended"] and play_requested:
            return state
        if state and state["duration"] and not state["paused"]:
            now = time.monotonic()
            if playback_seen_at is None:
                playback_seen_at = now
            # UniNet can restore the saved position just after the player starts.
            if resume_at is None or state["position"] >= resume_at - 3 or now - playback_seen_at >= 2:
                return state
        elif not play_requested and start.is_visible():
            click(start, player, timeout=4000)
            play_requested = True
        else:
            close_popup(card.page)
        time.sleep(0.25)
    raise PlaybackError("Плеер не начал воспроизведение.")


def watch_until_end(card, state, control):
    if state["ended"]:
        return
    player = frame(card)
    video = player.locator("video").first
    last_position = state["position"]
    last_sample = last_movement = time.monotonic()
    last_resume = 0
    while True:
        time.sleep(0.25 if control.paused else 0.75)
        control.poll()
        if card.page.is_closed():
            raise UniNetError("Окно Chrome закрыто. Просмотр остановлен.")
        state = media_state(player)
        now = time.monotonic()
        if state:
            sync_sound(video, state, control)
            if state["error"]:
                raise PlaybackError(f"Ошибка видео Kinescope (код {state['error']}).")
            if abs(state["rate"] - 1) > 0.001:
                raise UniNetError("У плеера изменилась скорость воспроизведения. Требуется 1×.")
            delta = state["position"] - last_position
            elapsed = now - last_sample
            if delta < -2 or delta > elapsed + 3:
                raise UniNetError(
                    f"Позиция плеера изменилась с {last_position:.1f} с до "
                    f"{state['position']:.1f} с за {elapsed:.1f} с. Просмотр остановлен."
                )
            if state["ended"]:
                return
            if delta > 0.05:
                last_movement = now
            last_position, last_sample = state["position"], now
            if control.paused:
                if not state["paused"]:
                    video.evaluate("video => video.pause()", timeout=2000)
                last_movement = now
                last_resume = 0
                continue
            if state["paused"] and now - last_resume >= 5:
                last_resume = now
                close_popup(card.page)
                play = player.get_by_role("button", name=PLAY).first
                if play.is_visible():
                    click(play, player, timeout=4000)
                else:
                    video.evaluate("video => video.play().catch(() => {})", timeout=2000)
        if not control.paused and now - last_movement > 55:
            raise PlaybackError("Позиция видео не менялась 55 секунд.")


def wait_credit(card, index, seconds, control):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        deadline += control.wait_until_running()
        if read_lecture(card).credited(index):
            return True
        time.sleep(0.8)
    return False


def reload_lecture(page):
    page.reload(wait_until="domcontentloaded")
    if not at_student_portal(page.url):
        raise UniNetError("Сессия UniNet закончилась.")


def play_clip(page, card, lecture, index, control):
    if lecture.clips:
        select_clip(card, index)
    for attempt in range(3):
        try:
            control.wait_until_running()
            current = read_lecture(card)
            if current.credited(index):
                return
            resume_at = current.clips[index].resume_at if current.clips else None
            first_state = start_video(card, resume_at=resume_at, control=control)
            if abs(first_state["rate"] - 1) > 0.001:
                raise UniNetError("Плеер запущен со скоростью, отличной от 1×.")
            minutes, seconds = divmod(int(first_state["position"]), 60)
            if resume_at is not None and first_state["position"] > 3:
                log(f"Плеер продолжил с {minutes}:{seconds:02d} на 1×.", kind="video")
            else:
                log("Плеер запущен с начала на 1×.", kind="video")
            watch_until_end(card, first_state, control)
            return
        except (PlaybackError, BrowserTimeout) as error:
            if attempt == 2:
                raise UniNetError(f"Не удалось восстановить плеер. {error}") from None
            reason = str(error) if isinstance(error, PlaybackError) else "Плеер не ответил вовремя."
            log(f"{reason} Восстанавливаю плеер и повторяю запуск.", kind="retry")
            close_popup(page)
            if attempt == 0 and len(lecture.clips) > 1:
                select_clip(card, 1 if index == 0 else 0)
                select_clip(card, index)
            else:
                reload_lecture(page)
                if lecture.clips:
                    select_clip(card, index)


def process_course(page, *, check, control):
    locked_weeks = []
    for number in list_weeks(page):
        control.wait_until_running()
        open_week(page, number)
        main = page.locator("main")
        opens = main.get_by_text(re.compile(r"^Opens\s+\S.+$", re.I)).first
        if opens.is_visible():
            locked_weeks.append(number)
            log(f"Week {number} · {opens.inner_text()}.")
            continue
        if main.get_by_text("Week completed", exact=True).is_visible():
            log(f"Week {number} · все ролики засчитаны.")
            continue
        for card in lecture_cards(page):
            lecture = read_lecture(card)
            name = card.get_by_role("heading", level=3).inner_text()
            if lecture.completed:
                log(f"Week {number} · {name} · все ролики засчитаны.")
                continue
            if lecture.total and not lecture.clips and lecture.total != 1:
                raise UniNetError(f"Week {number} · список роликов не загрузился.")
            if check:
                log(f"Week {number} · {name} · просмотрено {lecture.watched}/{lecture.total}.")
                for clip in lecture.clips:
                    if not clip.credited:
                        log(f"  К просмотру — {clip.title}")
                continue
            for index in range(len(lecture.clips) or 1):
                control.wait_until_running()
                lecture = read_lecture(card)
                if lecture.credited(index):
                    continue
                title = lecture.clips[index].title if lecture.clips else "Видео"
                log(f"Week {number} · {title}", kind="video")
                play_clip(page, card, lecture, index, control)
                confirmed = wait_credit(card, index, 25, control)
                if not confirmed:
                    reload_lecture(page)
                    confirmed = wait_credit(card, index, 12, control)
                if not confirmed:
                    raise UniNetError(f"UniNet пока не засчитал Week {number} · {title}.")
                log("UniNet засчитал просмотр.", kind="done")
                if control.preferences.one_video:
                    log("Режим одного ролика завершён.", kind="done")
                    return
                if read_lecture(card).completed:
                    break
    if check:
        log("Проверка курса закончена.", kind="done")
    elif locked_weeks:
        weeks = ", ".join(f"Week {number}" for number in locked_weeks)
        log(f"Все доступные видео засчитаны. Портал пока не открыл {weeks}.", kind="done")
    else:
        log("Все видео курса засчитаны.", kind="done")


def run(*, check, credentials, control, keep_on_error=False):
    log("Открываю Google Chrome.")
    with sync_playwright() as playwright, keep_awake():
        browser = playwright.chromium.launch(
            channel="chrome", headless=False, args=["--start-maximized"]
        )
        context = browser.new_context(no_viewport=True, locale="en-US")
        page = context.new_page()
        page.set_default_timeout(10000)
        try:
            control.wait_until_running()
            open_course(page, credentials)
            try:
                process_course(page, check=check, control=control)
            except (BrowserError, AssertionError, UniNetError):
                login_page = origin(page.url) == SSO or page.url.startswith(f"{PORTAL}/login")
                if not login_page:
                    raise
                log("Сессия закончилась. Вхожу снова и проверяю прогресс.", kind="retry")
                open_course(page, credentials)
                process_course(page, check=check, control=control)
        except PlaybackStopped:
            log("Просмотр остановлен из консоли.", kind="control")
            return 0
        except (BrowserError, AssertionError, UniNetError) as error:
            if isinstance(error, UniNetError):
                message = str(error)
            elif at_student_portal(page.url):
                operation = str(error).partition(":")[0]
                detail = operation if re.fullmatch(r"\w+\.\w+", operation) else type(error).__name__
                message = f"Сбой на странице {urlsplit(page.url).path}. Операция {detail}."
            else:
                message = "Chrome не смог открыть нужную страницу UniNet."
            log(message, kind="error")
            if keep_on_error and not page.is_closed():
                log("Введи stop, чтобы закрыть Chrome.", kind="control")
                try:
                    while not page.is_closed():
                        control.poll()
                        page.wait_for_timeout(250)
                except (PlaybackStopped, BrowserError):
                    pass
            return 1
        finally:
            browser.close()
    return 0


def run_action(mode, vault, control):
    show_banner(mode)
    try:
        if mode == "setup":
            setup_credentials(vault)
            return 0
        saved = vault.get_password(VAULT_SERVICE, VAULT_ENTRY)
        if not saved:
            raise UniNetError("Сначала настрой вход через меню или команду run-uninet.cmd --setup.")
        credentials = json.loads(saved)
        chat = mode == "play" and interactive_terminal()

        def action():
            return run(check=mode == "check", credentials=credentials, control=control, keep_on_error=chat)

        return run_playback_chat(action, control.commands) if chat else action()
    except UniNetError as error:
        log(str(error), kind="error")
        return 1
    except BrowserError:
        log("Не удалось запустить Google Chrome.", kind="error")
        return 1


def settings_menu(preferences):
    while True:
        sound = "выключен" if preferences.muted else "включён"
        scope = "один ролик" if preferences.one_video else "вся очередь"
        choice = menu_choice((
            ("1 / sound", f"Звук плеера · {sound}"),
            ("2 / mode", f"Просмотр за запуск · {scope}"),
            ("0 / back", "Назад"),
        ))
        if choice in ("0", "back"):
            return preferences
        if choice in ("1", "sound"):
            preferences = replace(preferences, muted=not preferences.muted)
        elif choice in ("2", "mode"):
            preferences = replace(preferences, one_video=not preferences.one_video)
        else:
            continue
        try:
            save_preferences(preferences)
            log("Настройки сохранены.", kind="done")
        except OSError:
            log("Настройки действуют до закрытия консоли. Сохранение недоступно.", kind="retry")


def run_menu(vault, preferences):
    show_banner("menu")
    actions = {"1": "play", "play": "play", "2": "check", "check": "check", "4": "setup", "setup": "setup"}
    while True:
        sound = "звук выкл." if preferences.muted else "звук вкл."
        scope = "один ролик" if preferences.one_video else "вся очередь"
        choice = menu_choice((
            ("1 / play", "Начать просмотр"),
            ("2 / check", "Проверить прогресс"),
            ("3 / settings", f"Настройки · {sound} · {scope}"),
            ("4 / setup", "Настроить вход"),
            ("5 / support", "Поддержка"),
            ("0 / exit", "Выход"),
        ))
        if choice in ("0", "exit"):
            return 0
        if choice in ("3", "settings"):
            preferences = settings_menu(preferences)
        elif choice in ("5", "support"):
            open_support()
        elif choice in actions:
            mode = actions[choice]
            control = PlaybackControl(preferences)
            run_action(mode, vault, control)
            preferences = control.preferences


def main():
    parser = argparse.ArgumentParser(description=f"{NAME} — видеолекции {COURSE_NAME} в KBTU UniNet")
    parser.add_argument("--version", action="version", version=f"{NAME} {VERSION}")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--play", dest="mode", action="store_const", const="play", help="сразу начать просмотр")
    modes.add_argument("--setup", dest="mode", action="store_const", const="setup", help="настроить вход в KBTU")
    modes.add_argument("--check", dest="mode", action="store_const", const="check", help="показать прогресс")
    modes.add_argument("--support", dest="mode", action="store_const", const="support", help="открыть раздел поддержки")
    mode = parser.parse_args().mode
    if mode == "support":
        return open_support()
    interactive = interactive_terminal()
    if mode is None and not interactive:
        show_banner("menu")
        log("Открой меню в консоли Windows. Для прямого запуска используй run-uninet.cmd --play.")
        return 0

    configure_log(mode or "menu")
    try:
        preferences = load_preferences()
    except (OSError, ValueError):
        log("Не удалось прочитать настройки. Использую значения по умолчанию.", kind="retry")
        preferences = Preferences()
    vault = WinVaultKeyring()
    try:
        control = PlaybackControl(preferences)
        if mode != "setup" and interactive and not vault.get_password(VAULT_SERVICE, VAULT_ENTRY):
            log("Первый запуск. Сначала настрой вход в KBTU UniNet.")
            result = run_action("setup", vault, control)
            if result:
                return result
        if mode:
            return run_action(mode, vault, control)
        return run_menu(vault, preferences)
    except KeyringError:
        log("Диспетчер учётных данных Windows недоступен.", kind="error")
        return 1
    except EOFError:
        return 0
    except KeyboardInterrupt:
        log("Просмотр остановлен.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
