"""Все тексты бота (Telegram HTML)."""

from __future__ import annotations

from bot.db import Skill, User
from bot.formatting import esc, esc_attr, plural, progress_bar
from bot.services.quota import QuotaStatus
from bot.sources.base import CATEGORY_META, Category

STAR = "✱"
SKILL_NAME_MAX = 40
SKILL_BODY_MAX = 2000


def main_menu(bot_name: str, quota: QuotaStatus, watches: int) -> str:
    if watches:
        w = f"{watches} {plural(watches, 'активная', 'активные', 'активных')}"
    else:
        w = "пока нет"
    q = "безлимит" if quota.unlimited else f"{progress_bar(quota.pct)} {quota.pct}%"
    return (
        f"{STAR} <b>{esc(bot_name)}</b> · ИИ-аналитик\n\n"
        f"Запас на сегодня {q}\n"
        f"Слежки — {w}\n\n"
        "Напиши тикер или вопрос — начну разбор."
    )


def analyze_screen(hours: int, has_fallback: bool) -> str:
    legend = f"<i>🔥 — чаще всего спрашивают за {hours} ч</i>"
    if has_fallback:
        legend += "\n<i>⭐ — популярные монеты</i>"
    return (
        f"{STAR} <b>Разбор монеты</b>\n\n"
        "Напиши тикер, адрес контракта или вопрос своими словами.\n"
        "<i>Например: SOL · $HYPE · сравни ARB и OP</i>\n\n"
        f"{legend}"
    )


# --- навыки -------------------------------------------------------------------------------


def skills_screen(skills: list[Skill], limit: int) -> str:
    text = (
        "🧠 <b>Навыки</b>\n\n"
        "Сохрани свои правила или шаблон один раз — и зови по имени в вопросе.\n"
        "<i>Например: «разбери BTC, навык тейки»</i>"
    )
    if skills:
        text += f"\n\nТвои навыки: {len(skills)} из {limit}"
    return text


SKILL_ASK_NAME = (
    "📘 Как назовём навык? Пришли короткое имя (до 40 символов), например: "
    "<b>тейки</b> или <b>мой стиль</b>. Этим словом ты будешь звать навык в вопросах."
)


def skill_ask_body(name: str) -> str:
    return (
        f"✍️ Теперь пришли сам навык «<b>{esc(name)}</b>»: правила, шаблон ответа или чек-лист "
        f"(до {SKILL_BODY_MAX} символов).\n\n"
        "<i>Например: «Всегда давай три тейка — консервативный, базовый и агрессивный, "
        "и стоп под ближайшей поддержкой. В конце — риск/прибыль».</i>"
    )


def skill_saved(name: str) -> str:
    return f"✅ Навык «<b>{esc(name)}</b>» сохранён.\n\nЗови его так: <i>«разбери BTC, навык {esc(name)}»</i>"


def skill_view(skill: Skill) -> str:
    return (
        f"📘 <b>{esc(skill.name)}</b>\n\n"
        f"{esc(skill.content)}\n\n"
        f"<i>Как вызвать: «разбери BTC, навык {esc(skill.name)}»</i>"
    )


def skill_delete_confirm(skill: Skill) -> str:
    return f"🗑 Удалить навык «<b>{esc(skill.name)}</b>»?"


SKILL_NAME_EMPTY = "Имя не может быть пустым. Пришли короткое слово, например: <b>тейки</b>."
SKILL_NAME_LONG = f"Слишком длинно — до {SKILL_NAME_MAX} символов. Попробуй короче."
SKILL_NAME_TAKEN = "Навык с таким именем уже есть. Придумай другое имя."
SKILL_BODY_LONG = f"Слишком длинно — до {SKILL_BODY_MAX} символов. Сократи и пришли ещё раз."
SKILL_LIMIT = "Достигнут лимит навыков. Удали ненужный, чтобы добавить новый."
SEND_TEXT = "Пришли, пожалуйста, текстом."


# --- слежки -------------------------------------------------------------------------------


def watches_screen(lines: list[str], limit: int) -> str:
    text = (
        "🔔 <b>Слежки</b>\n\n"
        "Напишу, когда цена дойдёт до уровня или монета резко двинется.\n"
        "<i>Например: BTC &gt; 120000 · ETH &lt; 3000 · SOL ±5%</i>"
    )
    if lines:
        text += f"\n\n<b>Активные ({len(lines)} из {limit}):</b>\n" + "\n".join(f"• {ln}" for ln in lines)
        text += "\n\n<i>Нажми на слежку ниже, чтобы удалить.</i>"
    return text


WATCH_ASK = (
    "🔔 Пришли условие: тикер и уровень цены или процент.\n\n"
    "<i>Например: BTC &gt; 120000 · ETH &lt; 3000 · SOL ±5% · TON +10%</i>"
)


def watch_ask_for(symbol: str, price: str | None) -> str:
    now = f" (сейчас {price})" if price else ""
    return (
        f"🔔 Слежка за <b>{esc(symbol)}</b>{now}.\n"
        "Пришли уровень или процент:\n\n"
        "<i>&gt; 120000 · &lt; 95000 · ±5% · +10%</i>"
    )


WATCH_BAD = (
    "Не понял условие 🤔 Примеры:\n"
    "<code>BTC &gt; 120000</code> · <code>ETH &lt; 3000</code> · <code>SOL ±5%</code> · <code>TON +10%</code>"
)
WATCH_LIMIT = "Достигнут лимит слежек. Удали ненужную, чтобы добавить новую."


def watch_no_price(symbol: str) -> str:
    return f"Не нашёл цену {esc(symbol)} на биржах. Проверь тикер и пришли условие ещё раз."


def watch_saved(desc: str, price: str) -> str:
    return f"✅ Слежка добавлена: <b>{desc}</b>\nСейчас: {price}. Проверяю каждую минуту."


def watch_already(desc: str) -> str:
    return f"Условие уже выполнено: {desc}. Поставь другой уровень."


def watch_triggered(desc: str, price: str) -> str:
    return f"🔔 <b>Слежка сработала</b>\n{desc}\nСейчас: <b>{price}</b>"


# --- профиль и помощь -----------------------------------------------------------------------


def profile(user: User | None, user_id: int, quota: QuotaStatus, total: int, skills: int, watches: int) -> str:
    name = esc(user.first_name or user.username or "—") if user else "—"
    since = user.created_at[:10] if user else "—"
    if quota.unlimited:
        q = "безлимит"
    else:
        q = f"{progress_bar(quota.pct)} {quota.pct}% ({quota.left} из {quota.limit})"
    plan = {"free": "Free"}.get(user.plan, user.plan) if user else "Free"
    return (
        "👤 <b>Профиль</b>\n\n"
        f"Имя: {name}\n"
        f"ID: <code>{user_id}</code>\n"
        f"Тариф: {esc(plan)}\n"
        f"Запас на сегодня: {q}\n"
        f"Разборов всего: {total}\n"
        f"Навыков: {skills} · Слежек: {watches}\n"
        f"С нами с {since}"
    )


HELP_SKILLS = (
    "🔎 Разбор монеты — цена, биржи и спред, объёмы, капа, ATH\n"
    "📈 Деривативы — фандинг, открытый интерес, лонги/шорты, опционы\n"
    "🛡 Проверка контракта — honeypot, налоги, ликвидность, держатели\n"
    "⚖️ Сравнение — до трёх монет в одной таблице\n"
    "🌍 Рынок — капа, доминация BTC, страх и жадность, макро\n"
    "⛓ Ончейн и DeFi — MVRV, активность сети, TVL, выручка\n"
    "📰 Новости и соцсети — 30+ лент, Reddit, 4chan, StockTwits\n"
    "🐝 Рой агентов — ИИ-специалисты + главный аналитик\n"
    "🔔 Слежки — напишу, когда цена дойдёт до уровня\n"
    "🧠 Навыки — твои правила и шаблоны ответа"
)

HELP_EXAMPLES = (
    "<code>разбери $HYPE</code>\n"
    "<code>что с фандингом и OI по BTC</code>\n"
    "<code>сравни ARB и OP</code>\n"
    "<code>0x6982508145454Ce325dDbE47a25d4ec3d2311933</code> — проверка на скам\n"
    "<code>что сейчас на рынке?</code>\n"
    "<code>разбери SOL, навык тейки</code>\n"
    "<code>BTC &gt; 120000</code> — в разделе 🔔 Слежки"
)


def help_text(
    bot_username: str | None,
    limit: int,
    quota: QuotaStatus,
    stats: dict[str, int],
    ai: str,
    rules_url: str = "",
) -> str:
    mention = f"@{esc(bot_username)}" if bot_username else "меня"
    if quota.unlimited:
        left = "у тебя безлимит"
    else:
        left = f"сегодня осталось {quota.left} из {quota.limit}"
    ai_line = "рой ИИ-агентов" if ai.startswith("рой") else ("ИИ-аналитик" if ai != "выключен" else "выводы по правилам")
    parts = [
        "❓ <b>Помощь</b>\n\n"
        "Пиши как в чате: тикер, вопрос или пересланный пост — соберу данные "
        f"из {stats['active']} источников и отвечу с цифрами. Разделы ниже раскрываются нажатием.",
        f"<b>Что умею</b>\n<blockquote expandable>{HELP_SKILLS}</blockquote>",
        f"<b>Примеры вопросов</b>\n<blockquote expandable>{HELP_EXAMPLES}</blockquote>",
        "<b>В чатах</b>\n<blockquote>"
        f"Добавь меня в группу и упомяни {mention} с вопросом — или ответь на моё сообщение.\n"
        "Ещё можно командой: <code>/ask BTC</code></blockquote>",
        "<b>Лимиты</b>\n<blockquote>"
        f"{limit} {plural(limit, 'разбор', 'разбора', 'разборов')} в сутки, запас обновляется в 00:00 UTC — {left}.\n"
        "Слежки работают без лимита.</blockquote>",
        f"<i>Сейчас работает: {ai_line} · /sources — все источники. Не финансовый совет.</i>",
    ]
    if rules_url:
        parts.append(f'<a href="{esc_attr(rules_url)}">Правила использования</a>')
    return "\n\n".join(parts)


def sources_screen(stats: dict[str, int], per_cat: dict[Category, tuple[int, int, int]]) -> str:
    lines = [
        "🔌 <b>Источники данных</b>\n",
        f"В каталоге: <b>{stats['total']}</b>",
        f"✅ Подключено: <b>{stats['active']}</b> · 🔑 ждут API-ключ: {stats['needs_key']} · "
        f"🛠 в планах: {stats['planned']}" + (f" · ⏸ выключено: {stats['disabled']}" if stats["disabled"] else ""),
        "",
    ]
    for cat, (active, implemented, total) in per_cat.items():
        icon, label = CATEGORY_META[cat]
        lines.append(
            f"{icon} {label} — {active} из {total}"
            + (f" (+{implemented - active} с ключом)" if implemented > active else "")
        )
    lines.append("\n<i>Полный список — docs/SOURCES.md в репозитории.</i>")
    return "\n".join(lines)


# --- разбор ----------------------------------------------------------------------------------


def quota_empty(limit: int) -> str:
    return f"⏳ Запас на сегодня закончился ({limit} разборов). Обновится в 00:00 UTC.\nСлежки продолжают работать."


BUSY = "⏳ Ещё разбираю прошлый запрос — секунду…"
NOT_FOUND = (
    "Не нашёл монету в вопросе 🤔\n\n"
    "Напиши тикер (<code>BTC</code>, <code>$HYPE</code>), адрес контракта "
    "или спроси про рынок: <i>что сейчас на рынке?</i>"
)


def not_listed(what: str) -> str:
    return f"Не нашёл данных по <b>{esc(what)}</b> 🤷\nПроверь тикер или пришли адрес контракта. Запас не списан."


GROUP_HINT = "Спроси меня так: <code>@{bot} BTC</code> или <code>/ask BTC</code>"

ERROR = "😵 Что-то пошло не так при разборе. Попробуй ещё раз через минуту — запас не списан."
NO_LAST = "Не помню прошлый запрос — пришли его ещё раз."
