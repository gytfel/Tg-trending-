"""Конвейер разбора: запрос → активы → параллельный опрос источников → отчёт → ИИ-вывод."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Protocol

from bot.db import Skill
from bot.formatting import esc, short_addr
from bot.query_parser import ParsedQuery
from bot.render import Card
from bot.services import cards, prose, report, swarm
from bot.services.llm import LLM, to_html
from bot.services.progress import Progress
from bot.services.swarm import Swarm
from bot.sources.base import Asset, Category
from bot.sources.collector import Snapshot, collect
from bot.sources.http import Http
from bot.sources.registry import Registry
from bot.sources.resolver import resolve_contract, resolve_symbol

log = logging.getLogger(__name__)


class LiveSink(Protocol):
    """Куда показывать ответ ИИ по мере генерации (реализация для Telegram — handlers/live.py)."""

    started: bool

    async def begin(self, card: Card | None, caption_head: str, text_prefix: str) -> None: ...

    async def update(self, body_html: str) -> None: ...


# короткие названия категорий для строки «Собрал данные: …»
CAT_SHORT = {
    Category.CEX: "биржи",
    Category.DERIVATIVES: "деривативы",
    Category.AGGREGATOR: "агрегаторы",
    Category.DEX: "DEX",
    Category.DEFI: "DeFi",
    Category.ONCHAIN: "ончейн",
    Category.SECURITY: "проверки",
    Category.SENTIMENT: "настроения",
    Category.NEWS: "новости",
    Category.SOCIAL: "соцсети",
    Category.MACRO: "макро",
}


# что бот «читает» прямо сейчас — по категории последнего ответившего источника
READING = {
    Category.CEX: ("💱", "Сверяю цены на биржах"),
    Category.DERIVATIVES: ("📈", "Смотрю фандинг и открытый интерес"),
    Category.AGGREGATOR: ("📊", "Сверяю капитализацию и объёмы"),
    Category.DEX: ("🦄", "Смотрю пулы на DEX"),
    Category.DEFI: ("🧱", "Смотрю TVL и выручку протоколов"),
    Category.ONCHAIN: ("⛓", "Считаю ончейн-метрики"),
    Category.SECURITY: ("🛡", "Проверяю контракт"),
    Category.SENTIMENT: ("🌡", "Смотрю настроение рынка"),
    Category.NEWS: ("📰", "Читаю новости"),
    Category.SOCIAL: ("💬", "Читаю соцсети"),
    Category.MACRO: ("🌍", "Смотрю макро-фон"),
}


def describe_task(q: ParsedQuery, skills: list[Skill]) -> str:
    """«Понял задачу: …» — первая строка размышлений."""
    targets = q.targets
    if not targets:
        what = "обзор рынка" + (" и ответ на вопрос" if not q.market else "")
    elif len(targets) > 1:
        what = "сравнение " + " и ".join(v if k == "symbol" else short_addr(v) for k, v in targets)
    elif targets[0][0] == "contract":
        what = f"проверка контракта {short_addr(targets[0][1])}"
    else:
        what = f"разбор {targets[0][1]}"
    if skills:
        what += " · навык «" + "», «".join(s.name for s in skills) + "»"
    return f"Понял задачу: {what}"


def describe_asset(a: Asset) -> str:
    if a.contract:
        name = a.name or a.symbol or short_addr(a.contract)
        return f"{name} · {a.chain}" if a.chain else f"{name} · сеть не определена"
    label = f"{a.name} ({a.symbol})" if a.name and a.symbol else (a.symbol or "?")
    return f"{label} #{a.rank}" if a.rank else label


def describe_collected(snaps: list[Snapshot]) -> str:
    ok = sum(s.ok_count() for s in snaps)
    asked = sum(s.asked for s in snaps)
    by_cat = Counter(r.category for s in snaps for r in s.results)
    top = ", ".join(f"{CAT_SHORT[c]} {n}" for c, n in by_cat.most_common(4))
    return f"Собрал данные: {ok} из {asked}" + (f" · {top}" if top else "")


@dataclass(slots=True)
class AnalysisResult:
    kind: str  # asset | compare | contract | overview | nothing
    text: str
    symbols: list[str] = field(default_factory=list)  # для статистики «чаще спрашивают»
    snapshots: list[Snapshot] = field(default_factory=list)
    background: Snapshot | None = None
    asked: int = 0
    ok: int = 0
    elapsed: float = 0.0
    used_llm: bool = False
    agents: list[swarm.AgentResult] = field(default_factory=list)
    card: Card | None = None  # данные для картинки-карточки (рисует handler)
    caption: str = ""  # живой текст под карточкой (ИИ или по шаблону)

    @property
    def found(self) -> bool:
        return self.kind != "nothing"


class Analyzer:
    def __init__(
        self,
        http: Http,
        registry: Registry,
        llm: LLM,
        *,
        swarm: Swarm | None = None,
        source_timeout: float = 8.0,
        deadline: float = 15.0,
    ) -> None:
        self.http = http
        self.registry = registry
        self.llm = llm
        self.swarm = swarm
        self.source_timeout = source_timeout
        self.deadline = deadline

    async def _collect(self, sources, asset: Asset | None, on_done=None) -> Snapshot:
        return await collect(
            self.http,
            sources,
            asset,
            per_source_timeout=self.source_timeout,
            deadline=self.deadline,
            on_done=on_done,
        )

    async def _resolve(self, kind: str, value: str) -> Asset:
        if kind == "contract":
            return await resolve_contract(self.http, value)
        return await resolve_symbol(self.http, value)

    async def _collect_all(self, jobs: list[tuple[list, Asset | None]], progress: Progress) -> list[Snapshot]:
        """Опрашивает источники. В «Думаю…» — что сейчас читаем (по последнему ответившему) и счётчик."""
        total = sum(len(srcs) for srcs, _ in jobs)
        state = {"done": 0, "cat": None}

        def tick(src) -> None:
            state["done"] += 1
            state["cat"] = src.category

        def icon() -> str:
            return READING.get(state["cat"], ("🔧", ""))[0]

        def text() -> str:
            what = READING.get(state["cat"], ("", "Собираю данные"))[1]
            return f"{what} · {state['done']} из {total}"

        await progress.step(icon, text)
        snaps = list(await asyncio.gather(*(self._collect(srcs, a, tick) for srcs, a in jobs)))
        await progress.update(describe_collected(snaps), icon="📡")
        await progress.step("🧮", "Считаю: средняя цена, спред, фандинг, риски")
        return snaps

    async def analyze(
        self,
        q: ParsedQuery,
        skills: list[Skill],
        progress: Progress | None = None,
        live: LiveSink | None = None,
    ) -> AnalysisResult:
        """live — куда показывать ответ ИИ прямо по мере генерации (см. handlers/live.py)."""
        t0 = time.monotonic()
        progress = progress if progress is not None else Progress()

        targets = q.targets
        ai_on = self.llm.enabled or (self.swarm is not None and self.swarm.enabled)
        overview = not targets and (q.market or (ai_on and q.words >= 3))
        if not targets and not overview:
            return AnalysisResult(kind="nothing", text="")
        await progress.step("🧩", describe_task(q, skills))

        if overview:
            (g,) = await self._collect_all([(self.registry.for_overview(), None)], progress)
            body = report.overview_section(g)
            news = report.news_block(g, limit=5)
            card = cards.build_card("overview", [], g)
            verdict, used, agents, ai_html = await self._verdict(
                q, [], g, skills, progress, live=live, card=card, prefix=[body, news]
            )
            text = "\n\n".join(
                p for p in (body, news, verdict, report.footer(g.ok_count(), g.asked, time.monotonic() - t0)) if p
            )
            return AnalysisResult(
                kind="overview",
                text=text,
                background=g,
                asked=g.asked,
                ok=g.ok_count(),
                elapsed=time.monotonic() - t0,
                used_llm=used,
                agents=agents,
                card=card,
                caption=self._caption("overview", [], g, ai_html, agents, skills),
            )

        labels = ", ".join(v if k == "symbol" else short_addr(v) for k, v in targets)
        await progress.step("🔎", f"Ищу {labels} в справочниках монет")
        assets = list(await asyncio.gather(*(self._resolve(k, v) for k, v in targets)))
        await progress.update("Нашёл: " + "; ".join(describe_asset(a) for a in assets))

        jobs = [(self.registry.for_asset(a), a) for a in assets] + [(self.registry.for_global(), None)]
        *snaps, g = await self._collect_all(jobs, progress)

        found = [s for s in snaps if s.price() is not None or s.dex_pairs()]
        if not found:
            await progress.step("🤷", "Котировок и пулов по запросу не нашлось")
            return AnalysisResult(kind="nothing", text="", snapshots=snaps, background=g)

        sections = []
        compare = len(snaps) > 1
        if compare:
            sections.append(report.compare_table(found))
        for s in snaps:
            sections.append(report.contract_section(s) if s.asset and s.asset.contract else report.asset_section(s))
            if not compare:
                nb = report.news_block(s)
                if nb:
                    sections.append(nb)
        ml = report.market_line(g)
        if ml:
            sections.append(ml)

        kind = "compare" if compare else ("contract" if assets[0].contract else "asset")
        card = cards.build_card(kind, found, g)
        verdict, used, agents, ai_html = await self._verdict(
            q, found, g, skills, progress, live=live, card=card, prefix=list(sections)
        )
        if verdict:
            sections.append(verdict)

        asked = sum(s.asked for s in snaps) + g.asked
        ok = sum(s.ok_count() for s in snaps) + g.ok_count()
        elapsed = time.monotonic() - t0
        sections.append(report.footer(ok, asked, elapsed))

        symbols = [s.asset.symbol for s in found if s.asset and s.asset.symbol and not s.asset.contract]
        return AnalysisResult(
            kind=kind,
            text="\n\n".join(sections),
            symbols=symbols,
            snapshots=snaps,
            background=g,
            asked=asked,
            ok=ok,
            elapsed=elapsed,
            used_llm=used,
            agents=agents,
            card=card,
            caption=self._caption(kind, found, g, ai_html, agents, skills),
        )

    @staticmethod
    def _caption_head(agents: list[swarm.AgentResult], skills: list[Skill], ai: bool) -> str:
        """Первые строки подписи: итог роя и подключённый навык."""
        head = []
        ok = [a for a in agents if a.ok]
        if ok:
            counts = " · ".join(
                f"{icon} {n}" for sig, icon in swarm.SIGNAL_ICON.items() if (n := sum(1 for a in ok if a.signal == sig))
            )
            head.append(f"🐝 Рой из {len(ok)} {'агента' if len(ok) == 1 else 'агентов'}: {counts}")
        if skills and ai:
            head.append("🧠 Навык: " + esc(", ".join(s.name for s in skills)))
        return "\n".join(head)

    @classmethod
    def _caption(
        cls,
        kind: str,
        found: list[Snapshot],
        g: Snapshot | None,
        ai_html: str | None,
        agents: list[swarm.AgentResult],
        skills: list[Skill],
    ) -> str:
        """Живой текст под карточкой: ответ ИИ, а без него — связный текст по шаблону."""
        head = cls._caption_head(agents, skills, ai=bool(ai_html))
        if ai_html:
            body = ai_html
        elif kind == "overview" and g is not None:
            body = prose.overview(g)
        elif kind == "compare":
            body = prose.compare(found)
        elif kind == "contract" and found:
            body = prose.contract(found[0])
        elif found:
            body = prose.asset(found[0], g)
        else:
            body = ""
        return head + ("\n\n" if head and body else "") + body

    async def _verdict(
        self,
        q: ParsedQuery,
        snaps: list[Snapshot],
        g: Snapshot | None,
        skills: list[Skill],
        progress: Progress,
        *,
        live: LiveSink | None = None,
        card: Card | None = None,
        prefix: list[str] | None = None,
    ) -> tuple[str, bool, list[swarm.AgentResult], str | None]:
        """Рой агентов (если включён) → итог главного аналитика → или вывод по правилам.

        Возвращает: блок для подробного отчёта, был ли ИИ, агентов и HTML ответа ИИ (для подписи).
        """
        blocks: list[str] = []
        agents: list[swarm.AgentResult] = []
        if self.swarm is not None and self.swarm.enabled:

            async def started(roles: list[swarm.Role]) -> None:
                await progress.step("🐝", "Подключаю агентов: " + ", ".join(r.title.lower() for r in roles))

            async def finished(res: swarm.AgentResult) -> None:
                if res.ok:
                    await progress.step(res.role.icon, f"{res.role.title}: {res.headline}")
                else:
                    await progress.step(res.role.icon, f"{res.role.title}: не ответил ({res.error})")

            try:
                agents = await self.swarm.run(q.raw, snaps, g, on_start=started, on_done=finished)
            except Exception:  # noqa: BLE001
                log.exception("swarm failed")
                agents = []
            if agents:
                blocks.append(swarm.render(agents))
        answered = [a.as_dict() for a in agents if a.ok]

        if self.llm.enabled:
            if answered:
                await progress.step("✍️", f"Свожу выводы {len(answered)} агентов и пишу ответ")
            else:
                await progress.step("✍️", "Анализирую данные и пишу ответ")
            title = "🤖 <b>Итог</b>" if answered else "🤖 <b>Вывод</b>"
            if skills:
                title += f" · навык: {esc(', '.join(s.name for s in skills))}"
            on_text = None
            if live is not None:
                head = self._caption_head(agents, skills, ai=True)
                caption_head = head + "\n\n" if head else ""
                text_prefix = "\n\n".join(p for p in [*(prefix or []), *blocks, title] if p) + "\n"

                async def on_text(acc: str) -> None:
                    # первый кусок от модели: «Думаю…» уступает место ответу
                    if not live.started:
                        await live.begin(card, caption_head, text_prefix)
                    await live.update(to_html(acc))

            try:
                text = await self.llm.verdict(
                    q.raw, report.llm_context(snaps, g), skills, agents=answered or None, on_text=on_text
                )
            except Exception:  # noqa: BLE001
                log.exception("LLM verdict failed")
                text = None
            if text:
                ai_html = to_html(text)
                blocks.append(f"{title}\n{ai_html}")
                return "\n\n".join(blocks), True, agents, ai_html
        if snaps:
            await progress.step("✍️", "Пишу ответ")
            bullets = "\n".join(f"• {esc(b)}" for b in report.rule_verdict(snaps, g))
            note = "\n<i>Навыки применяются в ИИ-выводе — подключи ключ ИИ.</i>" if skills else ""
            blocks.append(f"🧭 <b>Коротко</b>\n{bullets}{note}")
        return "\n\n".join(blocks), bool(answered), agents, None
