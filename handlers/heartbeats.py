"""⏰ Tasks and bots: «tell me if the backup — or another bot — went
silent». Under the hood a dead-man switch: jobs ping the bot, silence
longer than the interval is an alert. They sit on the main screen next to
the sites, each with a card; the words «cron» and «curl» appear only on
that card, next to the ready-made line."""

import contextlib

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from handlers.common import HEARTBEAT_SECRET_NOTE, ack, back_button, cancel_kb, render
from services import heartbeats, public_urls, settings
from services.heartbeats import INTERVALS, every, job_states
from utils.text import esc

router = Router(name="heartbeats")


class AddHeartbeatForm(StatesGroup):
    name = State()


def _kind_rows(name: str) -> list[list[InlineKeyboardButton]]:
    return [[InlineKeyboardButton(text="🤖 Это бот или сервис", callback_data=f"hb_bot:{name}")],
            [InlineKeyboardButton(text="🗓 Это задача по расписанию", callback_data=f"hb_cron:{name}")]]


async def job_screen(name: str) -> tuple[str, InlineKeyboardMarkup]:
    """The card answers «what is this and is it fine?» first. A task that
    never marked asks one question — a bot, a scheduled task, or nothing
    of the kind — and the address with its how-to sits behind the answer:
    a raw curl line with a secret in it explained nothing to anybody."""
    job = next(j for j in await job_states() if j.name == name)
    n, rhythm = esc(name), every(job.interval)
    lines = [f"⏰ {n}"]
    rows: list[list[InlineKeyboardButton]] = []
    if job.icon == "✅":
        lines += [f"✅ {esc(job.state[:1].upper() + job.state[1:])}.", f"Выходит на связь {rhythm}. Замолчит — напишу."]
        rows.append([InlineKeyboardButton(text="🔗 Как подключена", callback_data=f"hb_ask:{name}")])
    elif job.icon == "🟠":
        lines += [f"🟠 {esc(job.state[:1].upper() + job.state[1:])}, а выходит на связь {rhythm}.", "",
                  "Обычно это значит, что задача не запустилась или упала, не дойдя до конца. Проверь её. "
                  "Если меняли адрес для отметок — подключи заново."]
        rows.append([InlineKeyboardButton(text="🔗 Подключить заново", callback_data=f"hb_ask:{name}")])
    else:
        lines += [f"{job.icon} Пока не выходит на связь · жду {rhythm}", "",
                  f"Я жду, что «{n}» будет выходить со мной на связь {rhythm} — так я узнаю, что работа "
                  f"запустилась и дошла до конца. Связи ещё не было: похоже, не подключили.", "",
                  f"Что такое «{n}» у тебя?"]
        rows += _kind_rows(name)
    status = await heartbeats.job_status(name)
    if status:
        lines[2:2] = ["", f"{esc(heartbeats.status_title(job, status))}:",
                      *(f"{icon} {esc(item)} · {esc(text)}" for icon, item, text in heartbeats.status_items(job, status))]
        if status.get("open_url"):
            rows.insert(0, [InlineKeyboardButton(text=f"↗️ Открыть {name}", url=status["open_url"])])
    rows.append([InlineKeyboardButton(text="🗑 Такой задачи нет — убрать" if job.icon not in ("✅", "🟠")
                                      else "🗑 Убрать", callback_data=f"hb_del:{name}")])
    rows.append([InlineKeyboardButton(text="← Боты", callback_data="blk:bots")])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)


def _url_line(name: str) -> str:
    return f"<code>{esc(public_urls.heartbeat_url(name))}</code>"


# A plain string, not an f-string: GitHub reads «${{ }}» itself.
GITHUB_STEP = ("<code>- run: curl -fsS \"$HEARTBEAT_URL\"\n"
               "  env:\n"
               "    HEARTBEAT_URL: ${{ secrets.HEARTBEAT_URL }}</code>")


def _back(name: str) -> InlineKeyboardMarkup:
    # Rotation is always one tap away, not only when the secrets happen to collide.
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔑 Адрес мог утечь — сменить", callback_data="setup:hb_regen")],
        [InlineKeyboardButton(text="← Назад", callback_data=f"hb_show:{name}")]])


@router.callback_query(F.data.startswith("hb_ask:"))
async def cb_hb_ask(call: CallbackQuery):
    name = call.data.split(":", 1)[1]
    if name not in settings.heartbeat_jobs():
        await ack(call, "Задачи больше нет", show_alert=True)
        return
    await ack(call)
    await render(call, f"⏰ {esc(name)}\n\nЧто такое «{esc(name)}» у тебя?",
                 InlineKeyboardMarkup(inline_keyboard=[*_kind_rows(name), *_back(name).inline_keyboard]))


@router.callback_query(F.data.startswith("hb_bot:"))
async def cb_hb_bot(call: CallbackQuery):
    name = call.data.split(":", 1)[1]
    await ack(call)
    await render(call, f"🤖 Подключить «{esc(name)}»\n\n"
                       f"Дай этому боту адрес — нажми на него, он скопируется:\n{_url_line(name)}\n\n"
                       f"Бот должен открывать его каждый раз, когда удачно поработал. В его настройках такое "
                       f"поле обычно называется «healthcheck URL» или «heartbeat URL» — например, у SocialBot "
                       f"это переменная <code>HEALTHCHECK_URL</code> на Railway.\n\n"
                       f"{HEARTBEAT_SECRET_NOTE}\n\n"
                       f"Как только связь появится, строка на главном экране станет ✅.", _back(name))


@router.callback_query(F.data.startswith("hb_cron:"))
async def cb_hb_cron(call: CallbackQuery):
    name = call.data.split(":", 1)[1]
    await ack(call)
    url = esc(public_urls.heartbeat_url(name))
    await render(call, f"🗓 Подключить «{esc(name)}»\n\n"
                       f"В самом конце задача должна открыть адрес — нажми на него, он скопируется:\n"
                       f"{_url_line(name)}\n{HEARTBEAT_SECRET_NOTE}\n\n"
                       f"Где задача запускается:\n"
                       f"• <b>GitHub Actions</b> — адрес в файл workflow не вставляй: репозиторий читают другие. "
                       f"В репозитории: Settings → Secrets and variables → Actions → New repository secret, "
                       f"имя <code>HEARTBEAT_URL</code>, значение — адрес выше. Последний шаг workflow:\n"
                       f"{GITHUB_STEP}\n"
                       f"• <b>cron на своём сервере</b> — допиши в конец строки задачи:\n"
                       f"<code>&& curl -fsS {url}</code>\n"
                       f"• <b>Планировщик хостинга</b> (Railway Cron, Render…) — положи адрес в переменную "
                       f"окружения сервиса <code>HEARTBEAT_URL</code> и допиши в конец команды "
                       f"<code>&& curl -fsS \"$HEARTBEAT_URL\"</code>; в render.yaml и railway.json адрес не пиши.\n\n"
                       f"Задача упадёт раньше — адрес не откроется, и я напишу. Как только связь появится, "
                       f"строка на главном экране станет ✅.", _back(name))


@router.callback_query(F.data.startswith("hb_show:") | F.data.startswith("hb_how:"))
async def cb_hb_show(call: CallbackQuery):
    """hb_show: from the main screen, edits it in place. hb_how: from the
    morning digest or an alert — answers below and leaves that message as is."""
    kind, name = call.data.split(":", 1)
    if name not in settings.heartbeat_jobs():
        await ack(call, "Задачи больше нет", show_alert=True)
        return
    await ack(call)
    text, kb = await job_screen(name)
    if kind == "hb_how" and call.message is not None:
        await call.message.answer(text, reply_markup=kb)
    else:
        await render(call, text, kb)


@router.callback_query(F.data == "hb_add")
async def cb_hb_add(call: CallbackQuery, state: FSMContext):
    await ack(call)
    await state.set_state(AddHeartbeatForm.name)
    await render(call, "⏰ Бот или задача по расписанию\n\n"
                       "Самая коварная поломка бэкапа, выгрузки или другого бота — тишина: он молча "
                       "перестаёт работать, и никто не замечает. Добавь его сюда — он будет выходить со мной "
                       "на связь после каждого запуска, а замолчит — я напишу.\n\n"
                       "Как назвать? Латиницей, коротко: например <code>socialbot</code> или "
                       "<code>site-backup</code>.", cancel_kb())


@router.message(AddHeartbeatForm.name)
async def msg_hb_name(message: Message, state: FSMContext):
    name = (message.text or "").strip().lower()
    if not settings.valid_job_name(name):
        await message.answer("Только латиница, цифры и дефис, до 20 символов. Попробуй ещё раз:",
                             reply_markup=cancel_kb())
        return
    if len(settings.ui_heartbeat_jobs()) >= settings.MAX_HB_JOBS:
        await state.clear()
        await message.answer(f"Больше {settings.MAX_HB_JOBS} задач добавить нельзя — удали ненужные.",
                             reply_markup=back_button())
        return
    await state.update_data(hb_name=name)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label, callback_data=f"hbint:{mins}") for label, mins in INTERVALS[:2]],
        [InlineKeyboardButton(text=label, callback_data=f"hbint:{mins}") for label, mins in INTERVALS[2:]],
        [InlineKeyboardButton(text="✖️ Отмена", callback_data="fsm_cancel")]])
    await message.answer(f"Как часто «{esc(name)}» будет выходить на связь?", reply_markup=kb)


@router.callback_query(F.data.startswith("hbint:"))
async def cb_hb_interval(call: CallbackQuery, state: FSMContext):
    name = (await state.get_data()).get("hb_name")
    await state.clear()
    arg = call.data.split(":", 1)[1]
    if not name or not arg.isdigit():
        await ack(call, "Начни заново: 🏠 Главная → «🤖 Боты» → «➕ Бот или задача»", show_alert=True)
        return
    await settings.add_heartbeat_job(name, int(arg))
    await ack(call, "Добавил ✅")
    await render(call, f"✅ «{esc(name)}» под присмотром и уже на главном экране.\n\n"
                       f"Осталось подключить. Что такое «{esc(name)}»?",
                 InlineKeyboardMarkup(inline_keyboard=[*_kind_rows(name), *back_button().inline_keyboard]))


@router.callback_query(F.data.startswith("hb_rm:"))
async def cb_hb_remove_here(call: CallbackQuery):
    """«🗑 Удалить» under an alert or the digest: remove the task and drop its
    buttons — the message itself stays as it was."""
    name = call.data.split(":", 1)[1]
    removed = await settings.remove_heartbeat_job(name)
    if removed:
        await heartbeats.forget_status(name)
    await ack(call, f"Удалил «{name}» — больше не жду" if removed else "Её уже нет")
    markup = getattr(call.message, "reply_markup", None)
    if removed and markup is not None:
        rows = [row for row in markup.inline_keyboard
                if not any((b.callback_data or "").split(":", 1)[-1] == name for b in row)]
        with contextlib.suppress(TelegramBadRequest):
            await call.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(inline_keyboard=rows) if rows else None)


@router.callback_query(F.data.startswith("hb_del:"))
async def cb_hb_del(call: CallbackQuery):
    name = call.data.split(":", 1)[1]
    from_env = settings.heartbeat_job_from_env(name)
    if not await settings.remove_heartbeat_job(name):
        await ack(call, "Её уже нет", show_alert=True)
        return
    await heartbeats.forget_status(name)
    await ack(call, "Удалил")
    note = ("\n\nОна была записана в переменной <code>HEARTBEAT_JOBS</code> на сервере бота. Там она "
            "может остаться — я её больше не жду." if from_env else "")
    await render(call, f"🗑 «{esc(name)}» больше не жду.{note}", back_button())
