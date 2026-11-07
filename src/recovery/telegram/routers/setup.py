"""Onboarding (/start) and /settings share the same steps.

``setup_mode`` in FSM data says where to go after a step: the next
onboarding step, or back to the settings menu. Every value is saved as soon
as it is chosen.
"""

from datetime import time

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from recovery import battery
from recovery.db.models import User
from recovery.domain import BatteryColor
from recovery.telegram.callbacks import BatteryCb, SettingsCb, SetupCb, encode_time
from recovery.telegram.hooks import SettingsChanged
from recovery.telegram.keyboards import build, chunk
from recovery.texts import ru
from recovery.timeparse import parse_time, parse_time_range, parse_timezone

ONBOARDING = "onboarding"
SETTINGS = "settings"

TIMEZONES = [
    ("Тбилиси", "Asia/Tbilisi"),
    ("Ереван", "Asia/Yerevan"),
    ("Москва", "Europe/Moscow"),
    ("Берлин", "Europe/Berlin"),
    ("Лондон", "Europe/London"),
    ("UTC", "UTC"),
]
MORNING_TIMES = ["07:30", "08:00", "08:30", "09:00", "09:30", "10:00"]
EVENING_TIMES = ["20:00", "20:30", "21:00", "21:30", "22:00", "22:30"]
SESSION_TIMES = ["09:00", "10:00", "11:00", "12:00", "15:00", "17:00", "18:00", "19:00"]
QUIET_PRESETS = [("22:00–08:00", "2200-0800"), ("23:00–09:00", "2300-0900")]


class SetupStates(StatesGroup):
    timezone = State()
    time_value = State()  # field name in FSM data "setup_field"
    quiet_hours = State()


def fmt(t: time | None) -> str:
    return t.strftime("%H:%M") if t else "—"


def summary(user: User) -> str:
    lines = [
        ru.SUMMARY_TIMEZONE.format(tz=user.timezone),
        ru.SUMMARY_CHECKINS.format(morning=fmt(user.morning_time), evening=fmt(user.evening_time)),
    ]
    if user.session_weekday is not None and user.session_time is not None:
        lines.append(
            ru.SUMMARY_SESSION.format(
                day=ru.WEEKDAYS_DATIVE_PLURAL[user.session_weekday],
                time=fmt(user.session_time),
                lead=user.session_brief_lead_hours,
            )
        )
    else:
        lines.append(ru.SUMMARY_NO_SESSION)
    lines.append(ru.SUMMARY_REMINDERS_ON if user.reminders_enabled else ru.SUMMARY_REMINDERS_OFF)
    if user.quiet_hours_start and user.quiet_hours_end:
        lines.append(
            ru.SUMMARY_QUIET.format(
                start=fmt(user.quiet_hours_start), end=fmt(user.quiet_hours_end)
            )
        )
    else:
        lines.append(ru.SUMMARY_NO_QUIET)
    lines.append(
        ru.SUMMARY_TRANSCRIPTS_ON if user.store_transcripts else ru.SUMMARY_TRANSCRIPTS_OFF
    )
    lines.append(
        ru.SUMMARY_BATTERY_ON.format(reserve=_reserve_text(user))
        if user.battery_enabled
        else ru.SUMMARY_BATTERY_OFF
    )
    return "\n".join(lines)


def _reserve_text(user: User) -> str:
    reserve = user.battery_reserve_pct
    return f"{reserve}%" if reserve is not None else ru.BATTERY_NO_RESERVE


def settings_keyboard(user: User) -> InlineKeyboardMarkup:
    def item(name: str) -> str:
        return SettingsCb(item=name).pack()

    return build(
        [
            [(ru.BTN_SET_TIMEZONE, item("tz"))],
            [(ru.BTN_SET_MORNING, item("morning")), (ru.BTN_SET_EVENING, item("evening"))],
            [(ru.BTN_SET_SESSION, item("session")), (ru.BTN_SET_QUIET, item("quiet"))],
            [(ru.BTN_SET_BATTERY, item("battery"))],
            [
                (
                    ru.BTN_REMINDERS_ON if user.reminders_enabled else ru.BTN_REMINDERS_OFF,
                    item("rem"),
                )
            ],
            [
                (
                    ru.BTN_TRANSCRIPTS_ON if user.store_transcripts else ru.BTN_TRANSCRIPTS_OFF,
                    item("trans"),
                )
            ],
        ]
    )


def _time_keyboard(field: str, options: list[str]) -> InlineKeyboardMarkup:
    buttons = [(t, SetupCb(field=field, v=encode_time(t)).pack()) for t in options]
    rows = chunk(buttons, 3)
    rows.append([(ru.BTN_OTHER, SetupCb(field=field, v="other").pack())])
    return build(rows)


# --- step prompts -----------------------------------------------------------------
async def ask_timezone(bot: Bot, user: User) -> None:
    buttons = [(label, SetupCb(field="tz", v=zone).pack()) for label, zone in TIMEZONES]
    rows = chunk(buttons, 3)
    rows.append([(ru.BTN_OTHER, SetupCb(field="tz", v="other").pack())])
    await bot.send_message(user.telegram_id, ru.ASK_TIMEZONE, reply_markup=build(rows))


async def ask_time(bot: Bot, user: User, field: str) -> None:
    prompts = {
        "morning": (ru.ASK_MORNING_TIME, MORNING_TIMES),
        "evening": (ru.ASK_EVENING_TIME, EVENING_TIMES),
        "stime": (ru.ASK_SESSION_TIME, SESSION_TIMES),
    }
    text, options = prompts[field]
    await bot.send_message(user.telegram_id, text, reply_markup=_time_keyboard(field, options))


async def ask_session_day(bot: Bot, user: User) -> None:
    buttons = [(day, SetupCb(field="sday", v=str(i)).pack()) for i, day in enumerate(ru.WEEKDAYS)]
    rows = chunk(buttons, 4)
    rows.append([(ru.BTN_NO_SESSION, SetupCb(field="sday", v="none").pack())])
    await bot.send_message(user.telegram_id, ru.ASK_SESSION_DAY, reply_markup=build(rows))


async def ask_quiet_hours(bot: Bot, user: User) -> None:
    rows = [[(label, SetupCb(field="quiet", v=value).pack())] for label, value in QUIET_PRESETS]
    rows.append([(ru.BTN_QUIET_OFF, SetupCb(field="quiet", v="off").pack())])
    rows.append([(ru.BTN_OTHER, SetupCb(field="quiet", v="other").pack())])
    await bot.send_message(user.telegram_id, ru.ASK_QUIET_HOURS, reply_markup=build(rows))


async def show_settings(bot: Bot, user: User, prefix: str | None = None) -> None:
    text = ru.SETTINGS_TITLE.format(summary=summary(user))
    if prefix:
        text = f"{prefix}\n\n{text}"
    await bot.send_message(user.telegram_id, text, reply_markup=settings_keyboard(user))


# --- step transitions ---------------------------------------------------------------
async def after_step(
    field: str, bot: Bot, user: User, state: FSMContext, settings_changed: SettingsChanged
) -> None:
    data = await state.get_data()
    mode = data.get("setup_mode", SETTINGS)
    await state.set_state(None)
    if mode == ONBOARDING:
        following = {
            "tz": lambda: ask_time(bot, user, "morning"),
            "morning": lambda: ask_time(bot, user, "evening"),
            "evening": lambda: ask_session_day(bot, user),
            "sday": lambda: ask_time(bot, user, "stime"),
        }
        step = following.get(field)
        if step is not None:
            await step()
            return
        # Last step ("stime", or "sday" answered with "none").
        user.onboarding_completed = True
        await state.update_data(setup_mode=None)
        await settings_changed(user)
        await bot.send_message(
            user.telegram_id, ru.ONBOARDING_DONE.format(summary=summary(user), help=ru.HELP)
        )
        return
    if field == "sday" and user.session_weekday is not None:
        await ask_time(bot, user, "stime")
        return
    await settings_changed(user)
    await show_settings(bot, user, ru.SETTINGS_SAVED)


# --- handlers -----------------------------------------------------------------------
async def cmd_start(message: Message, bot: Bot, user: User, state: FSMContext) -> None:
    if user.onboarding_completed:
        await message.answer(ru.HELP)
        return
    await state.set_state(None)
    await state.update_data(setup_mode=ONBOARDING)
    await message.answer(ru.WELCOME)
    await ask_timezone(bot, user)


async def cmd_help(message: Message) -> None:
    await message.answer(ru.HELP)


async def cmd_settings(message: Message, bot: Bot, user: User, state: FSMContext) -> None:
    await state.update_data(setup_mode=SETTINGS)
    await show_settings(bot, user)


async def on_settings_item(
    cb: CallbackQuery,
    callback_data: SettingsCb,
    bot: Bot,
    user: User,
    state: FSMContext,
    settings_changed: SettingsChanged,
) -> None:
    await cb.answer()
    await state.update_data(setup_mode=SETTINGS)
    item = callback_data.item
    if item in ("rem", "trans"):
        if item == "rem":
            user.reminders_enabled = not user.reminders_enabled
        else:
            user.store_transcripts = not user.store_transcripts
        await settings_changed(user)
        if cb.message is not None:
            await cb.message.edit_text(  # type: ignore[union-attr]
                ru.SETTINGS_TITLE.format(summary=summary(user)),
                reply_markup=settings_keyboard(user),
            )
        return
    if item == "battery":
        await bot.send_message(
            user.telegram_id, battery_settings_text(user), reply_markup=battery_keyboard(user)
        )
        return
    prompts = {
        "tz": lambda: ask_timezone(bot, user),
        "morning": lambda: ask_time(bot, user, "morning"),
        "evening": lambda: ask_time(bot, user, "evening"),
        "session": lambda: ask_session_day(bot, user),
        "quiet": lambda: ask_quiet_hours(bot, user),
    }
    await prompts[item]()


async def on_setup_choice(
    cb: CallbackQuery,
    callback_data: SetupCb,
    bot: Bot,
    user: User,
    state: FSMContext,
    settings_changed: SettingsChanged,
) -> None:
    await cb.answer()
    if cb.message is not None:
        await cb.message.edit_reply_markup(reply_markup=None)  # type: ignore[union-attr]
    field, value = callback_data.field, callback_data.v

    if value == "other":
        if field == "tz":
            await state.set_state(SetupStates.timezone)
            await bot.send_message(user.telegram_id, ru.ASK_TIMEZONE_TEXT)
        elif field == "quiet":
            await state.set_state(SetupStates.quiet_hours)
            await bot.send_message(user.telegram_id, ru.ASK_QUIET_TEXT)
        else:
            await state.set_state(SetupStates.time_value)
            await state.update_data(setup_field=field)
            await bot.send_message(user.telegram_id, ru.ASK_TIME_TEXT)
        return

    if field == "tz":
        zone = parse_timezone(value)
        if zone is None:
            await bot.send_message(user.telegram_id, ru.TIMEZONE_BAD)
            return
        user.timezone = zone
    elif field in ("morning", "evening", "stime"):
        parsed = parse_time(f"{value[:2]}:{value[2:]}")
        if parsed is None:
            await bot.send_message(user.telegram_id, ru.TIME_BAD)
            return
        _set_time(user, field, parsed)
    elif field == "sday":
        if value == "none":
            user.session_weekday = None
            user.session_time = None
            field = "sday_none"
        else:
            user.session_weekday = int(value)
    elif field == "quiet":
        if value == "off":
            user.quiet_hours_start = user.quiet_hours_end = None
        else:
            start, end = value.split("-")
            user.quiet_hours_start = parse_time(f"{start[:2]}:{start[2:]}")
            user.quiet_hours_end = parse_time(f"{end[:2]}:{end[2:]}")
    await after_step(field, bot, user, state, settings_changed)


def _set_time(user: User, field: str, value: time) -> None:
    if field == "morning":
        user.morning_time = value
    elif field == "evening":
        user.evening_time = value
    elif field == "stime":
        user.session_time = value


async def on_timezone_text(
    message: Message, bot: Bot, user: User, state: FSMContext, settings_changed: SettingsChanged
) -> None:
    zone = parse_timezone(message.text or "")
    if zone is None:
        await message.answer(ru.TIMEZONE_BAD)
        return
    user.timezone = zone
    await after_step("tz", bot, user, state, settings_changed)


async def on_time_text(
    message: Message, bot: Bot, user: User, state: FSMContext, settings_changed: SettingsChanged
) -> None:
    value = parse_time(message.text or "")
    if value is None:
        await message.answer(ru.TIME_BAD)
        return
    field = (await state.get_data()).get("setup_field", "morning")
    _set_time(user, field, value)
    await after_step(field, bot, user, state, settings_changed)


async def on_quiet_text(
    message: Message, bot: Bot, user: User, state: FSMContext, settings_changed: SettingsChanged
) -> None:
    parsed = parse_time_range(message.text or "")
    if parsed is None:
        await message.answer(ru.QUIET_BAD)
        return
    user.quiet_hours_start, user.quiet_hours_end = parsed
    await after_step("quiet", bot, user, state, settings_changed)


# --- battery exercise settings ---------------------------------------------------------
def battery_settings_text(user: User) -> str:
    example_pct = 60
    return ru.BATTERY_SETTINGS.format(
        state=ru.BATTERY_ON if user.battery_enabled else ru.BATTERY_OFF,
        reserve=_reserve_text(user),
        color=f"{battery.SQUARES[BatteryColor(user.battery_color)]} "
        f"{ru.COLOR_LABELS[user.battery_color]}",
        example=battery.bar(example_pct, user.battery_color, user.battery_reserve_pct),
    )


def battery_keyboard(user: User) -> InlineKeyboardMarkup:
    return build(
        [
            [
                (
                    ru.BTN_BATTERY_ON if user.battery_enabled else ru.BTN_BATTERY_OFF,
                    BatteryCb(op="toggle").pack(),
                )
            ],
            [
                (ru.BTN_BATTERY_RESERVE, BatteryCb(op="reserve").pack()),
                (ru.BTN_BATTERY_COLOR, BatteryCb(op="color").pack()),
            ],
            [(ru.BTN_BACK, BatteryCb(op="back").pack())],
        ]
    )


def _reserve_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        (f"{pct}%", BatteryCb(op="setreserve", v=str(pct)).pack())
        for pct in battery.RESERVE_OPTIONS
    ]
    rows = chunk(buttons, 5)
    rows.append([(ru.BTN_NO_RESERVE, BatteryCb(op="setreserve", v="none").pack())])
    return build(rows)


def _color_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        (f"{square} {ru.COLOR_LABELS[color]}", BatteryCb(op="setcolor", v=color).pack())
        for color, square in battery.SQUARES.items()
    ]
    return build(chunk(buttons, 2))


async def on_battery_settings(
    cb: CallbackQuery, callback_data: BatteryCb, bot: Bot, user: User
) -> None:
    await cb.answer()
    op, value = callback_data.op, callback_data.v
    if op == "back":
        await show_settings(bot, user)
        return
    if op in ("reserve", "color"):
        text, keyboard = (
            (ru.ASK_BATTERY_RESERVE, _reserve_keyboard())
            if op == "reserve"
            else (ru.ASK_BATTERY_COLOR, _color_keyboard())
        )
        if cb.message is not None:
            await cb.message.edit_text(text, reply_markup=keyboard)  # type: ignore[union-attr]
        return
    if op == "toggle":
        user.battery_enabled = not user.battery_enabled
    elif op == "setreserve":
        user.battery_reserve_pct = None if value == "none" else int(value)
    elif op == "setcolor" and value in {c.value for c in BatteryColor}:
        user.battery_color = value
    if cb.message is not None:
        await cb.message.edit_text(  # type: ignore[union-attr]
            battery_settings_text(user), reply_markup=battery_keyboard(user)
        )


def make_router() -> Router:
    router = Router(name="setup")
    router.message.register(cmd_start, CommandStart())
    router.message.register(cmd_help, Command("help"))
    router.message.register(cmd_settings, Command("settings"))
    router.callback_query.register(on_settings_item, SettingsCb.filter())
    router.callback_query.register(on_battery_settings, BatteryCb.filter())
    router.callback_query.register(on_setup_choice, SetupCb.filter())
    not_command = ~F.text.startswith("/")
    router.message.register(on_timezone_text, SetupStates.timezone, F.text, not_command)
    router.message.register(on_time_text, SetupStates.time_value, F.text, not_command)
    router.message.register(on_quiet_text, SetupStates.quiet_hours, F.text, not_command)
    return router
