"""Журнал «размышлений» бота: короткие шаги обработки запроса.

Анализатор пишет сюда, что он делает («Понял задачу…», «Работаю с данными:
87 из 118»), а Telegram-реализация (handlers/thinking.py) показывает это в
одном сообщении, как ИИ, который думает над промтом. Базовый класс просто
копит шаги — этого достаточно для тестов и запуска без Telegram.
"""

from __future__ import annotations

from collections.abc import Callable

Text = str | Callable[[], str]  # текст (или иконка) шага или функция — для «живых» строк вроде счётчика


def resolve(text: Text) -> str:
    try:
        return text() if callable(text) else text
    except Exception:  # noqa: BLE001 — строка прогресса не должна ронять разбор
        return ""


class Progress:
    def __init__(self) -> None:
        self.steps: list[list] = []  # [иконка, текст]

    async def step(self, icon: Text, text: Text) -> None:
        """Новый шаг."""
        self.steps.append([icon, text])
        await self.changed()

    async def update(self, text: Text, icon: Text | None = None) -> None:
        """Заменить текст последнего шага (например, «работаю…» → «собрал…»)."""
        if not self.steps:
            await self.step(icon or "•", text)
            return
        self.steps[-1][1] = text
        if icon:
            self.steps[-1][0] = icon
        await self.changed()

    async def changed(self) -> None:
        """Хук для отображения; базовая версия ничего не показывает."""

    def lines(self) -> list[str]:
        return [f"{resolve(icon)} {resolve(text)}" for icon, text in self.steps]

    def current(self) -> tuple[str, str] | None:
        """Текущий шаг — его и показывает «Думаю…»."""
        if not self.steps:
            return None
        icon, text = self.steps[-1]
        return resolve(icon), resolve(text)
