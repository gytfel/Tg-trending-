"""Конвейер разбора: запрос → активы → параллельный опрос источников → отчёт → ИИ-вывод."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from bot.db import Skill
from bot.formatting import esc
from bot.query_parser import ParsedQuery
from bot.services import report
from bot.services.llm import LLM, to_html
from bot.sources.base import Asset
from bot.sources.collector import Snapshot, collect
from bot.sources.http import Http
from bot.sources.registry import Registry
from bot.sources.resolver import resolve_contract, resolve_symbol

log = logging.getLogger(__name__)

Stage = Callable[[str], Awaitable[None]]


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
        source_timeout: float = 8.0,
        deadline: float = 15.0,
    ) -> None:
        self.http = http
        self.registry = registry
        self.llm = llm
        self.source_timeout = source_timeout
        self.deadline = deadline

    async def _collect(self, sources, asset: Asset | None) -> Snapshot:
        return await collect(self.http, sources, asset, per_source_timeout=self.source_timeout, deadline=self.deadline)

    async def _resolve(self, kind: str, value: str) -> Asset:
        if kind == "contract":
            return await resolve_contract(self.http, value)
        return await resolve_symbol(self.http, value)

    async def analyze(self, q: ParsedQuery, skills: list[Skill], stage: Stage | None = None) -> AnalysisResult:
        t0 = time.monotonic()

        async def say(text: str) -> None:
            if stage is not None:
                try:
                    await stage(text)
                except Exception:  # noqa: BLE001 — прогресс не важнее результата
                    pass

        targets = q.targets
        overview = not targets and (q.market or (self.llm.enabled and q.words >= 3))
        if not targets and not overview:
            return AnalysisResult(kind="nothing", text="")

        if overview:
            sources = self.registry.for_overview()
            await say(f"⏳ Смотрю на рынок: опрашиваю {len(sources)} источников…")
            g = await self._collect(sources, None)
            body = report.overview_section(g)
            news = report.news_block(g, limit=5)
            verdict, used = await self._verdict(q, [], g, skills, say)
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
            )

        labels = ", ".join(v if k == "symbol" else v[:6] + "…" for k, v in targets)
        await say(f"⏳ Ищу {esc(labels)}…")
        assets = list(await asyncio.gather(*(self._resolve(k, v) for k, v in targets)))

        per_asset = [self.registry.for_asset(a) for a in assets]
        global_sources = self.registry.for_global()
        total = sum(len(s) for s in per_asset) + len(global_sources)
        await say(f"⏳ Опрашиваю {total} источников: биржи, деривативы, DEX, ончейн, новости…")

        g_task = asyncio.create_task(self._collect(global_sources, None))
        snaps = list(await asyncio.gather(*(self._collect(s, a) for s, a in zip(per_asset, assets, strict=True))))
        g = await g_task

        found = [s for s in snaps if s.price() is not None or s.dex_pairs()]
        if not found:
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

        verdict, used = await self._verdict(q, found, g, skills, say)
        if verdict:
            sections.append(verdict)

        asked = sum(s.asked for s in snaps) + g.asked
        ok = sum(s.ok_count() for s in snaps) + g.ok_count()
        elapsed = time.monotonic() - t0
        sections.append(report.footer(ok, asked, elapsed))

        kind = "compare" if compare else ("contract" if assets[0].contract else "asset")
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
        )

    async def _verdict(
        self,
        q: ParsedQuery,
        snaps: list[Snapshot],
        g: Snapshot | None,
        skills: list[Skill],
        say: Stage,
    ) -> tuple[str, bool]:
        if self.llm.enabled:
            await say("🤖 Данные собраны, формирую вывод…")
            try:
                text = await self.llm.verdict(q.raw, report.llm_context(snaps, g), skills)
            except Exception:  # noqa: BLE001
                log.exception("LLM verdict failed")
                text = None
            if text:
                title = "🤖 <b>Вывод</b>" + (f" · навык: {esc(', '.join(s.name for s in skills))}" if skills else "")
                return f"{title}\n{to_html(text)}", True
        if not snaps:
            return "", False
        bullets = "\n".join(f"• {esc(b)}" for b in report.rule_verdict(snaps, g))
        note = "\n<i>Навыки применяются в ИИ-выводе — подключи ANTHROPIC_API_KEY.</i>" if skills else ""
        return f"🧭 <b>Коротко</b>\n{bullets}{note}", False
