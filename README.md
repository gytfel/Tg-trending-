# Tg-trending — Telegram-бот «ИИ-аналитик» для крипторынка

Бот разбирает монету по тикеру, адресу контракта или вопросу своими словами.
Он параллельно опрашивает **130+ источников без ключей** (всего в каталоге 226),
собирает отчёт и даёт вывод через Claude. Ещё в боте есть слежки за ценой и
пользовательские «навыки» — сохранённые правила и шаблоны ответа.

```
✱ Trending · ИИ-аналитик

Запас на сегодня ▰▰▰▰▰▰▰▰▱▱ 76%
Слежки — пока нет

Напиши тикер или вопрос — начну разбор.

[ ✱ Разобрать монету ]
[ 🔔 Слежки ] [ 🧠 Навыки ]
[ 👤 Профиль ] [ ❓ Помощь ]
```

## Что умеет каркас

| Экран | Что делает |
|---|---|
| ✱ **Разобрать монету** | Экран с подсказкой и кнопками 🔥 «чаще всего спрашивают за 12 ч» (по статистике запросов, недостающие дополняются ⭐ популярными монетами) |
| Свободный текст | `BTC`, `$HYPE`, `сравни ARB и OP`, адрес `0x…` / Solana / TON, `что на рынке?` |
| Отчёт | Цена и изменение, капитализация, FDV, ATH, цены на биржах и спред, фандинг и OI, L/S, опционы (DVOL, Put/Call), DEX-пулы, TVL и комиссии, ончейн-метрики, соцсети, новости, рыночный фон (F&G, доминация BTC, DXY, S&P 500, золото) и **🤖 вывод ИИ** |
| Контракт | Ликвидность, объём, сделки, возраст пула, держатели и концентрация, **проверка на скам** (GoPlus, Honeypot.is, RugCheck) |
| 🔔 **Слежки** | `BTC > 120000`, `ETH < 3000`, `SOL ±5%`, `TON +10%`; фоновая проверка раз в минуту и уведомление со ссылкой на разбор |
| 🧠 **Навыки** | Пользователь сохраняет правила или шаблон («тейки», «мой стиль») и вызывает их в вопросе: «разбери BTC, навык тейки». Текст навыка уходит в промпт Claude |
| 👤 **Профиль** | Запас на сегодня, число разборов, навыков и слежек |
| ❓ **Помощь** / `/sources` | Справка и статистика источников по категориям |

Под отчётом есть кнопки «🔔 Следить», «🔄 Обновить» и «📊 Источники» (кто ответил, а кто нет).
Синие кнопки сделаны через `style="primary"` (Bot API 9.4+).

## Быстрый старт

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # впиши BOT_TOKEN от @BotFather, по желанию ANTHROPIC_API_KEY
python -m bot
```

Через Docker:

```bash
cp .env.example .env
docker compose up -d --build
```

Обязателен только `BOT_TOKEN`. Без `ANTHROPIC_API_KEY` бот работает целиком,
но вывод строится по простым правилам, а навыки не применяются.

### Проверка источников без Telegram

```bash
python -m bot.sources.probe BTC -v          # все источники по тикеру, что вернул каждый
python -m bot.sources.probe 0x6982508145454Ce325dDbE47a25d4ec3d2311933   # контракт
python -m bot.sources.probe --global        # общий фон рынка
python -m bot.sources.probe BTC --only binance,okx,coingecko -v
```

Запускай это на сервере, где будет жить бот: так сразу видно, какие API
доступны из его сети. Сломавшийся источник выключается без правки кода:
`DISABLED_SOURCES=id1,id2`.

## Источники данных

Полный список с id, статусом и ссылками — в [`docs/SOURCES.md`](docs/SOURCES.md)
(генерируется: `python -m bot.sources.probe --catalog docs/SOURCES.md`).

| Категория | Подключено (без ключа + с ключом) / в каталоге | Примеры |
|---|---|---|
| 🏦 Биржи (спот) | 30 / 38 | Binance, Bybit, OKX, Coinbase, Kraken, KuCoin, Gate, MEXC, Bitget, HTX, Bitfinex, Upbit… |
| 📈 Деривативы | 24 / 31 | фандинг и OI с 17 площадок (включая Hyperliquid, dYdX, Deribit), L/S Binance/Bybit/OKX, taker ratio, DVOL, Put/Call |
| 📊 Агрегаторы | 9 + 5 / 22 | CoinGecko, CoinPaprika, CryptoCompare, CoinLore, DefiLlama Prices; CMC, CoinCap, LiveCoinWatch… по ключу |
| 🦄 DEX | 5 + 1 / 16 | DexScreener, GeckoTerminal, Jupiter, тренды и бусты; Birdeye по ключу |
| 🧱 DeFi | 9 / 15 | DefiLlama: TVL протокола и сети, комиссии и выручка, доходности, раунды, стейблкоины, объём DEX; Lido |
| ⛓ Ончейн | 8 + 1 / 24 | mempool.space, Blockchain.com, Blockchair, Coin Metrics (MVRV, активные адреса), газ ETH, Solana RPC |
| 🛡 Безопасность | 3 / 9 | GoPlus (EVM и Solana), Honeypot.is, RugCheck |
| 🌡 Настроения | 1 + 2 / 9 | Fear & Greed; CMC F&G и LunarCrush по ключу |
| 📰 Новости | 33 + 2 / 40 | 30 RSS-лент (CoinDesk, Cointelegraph, The Block, Decrypt, ForkLog, Incrypted…), Google News EN/RU |
| 💬 Соцсети | 5 / 13 | Reddit, 4chan /biz/, StockTwits, Hacker News, Mastodon |
| 🌍 Макро | 3 + 1 / 9 | Yahoo Finance (DXY, S&P 500, Nasdaq, золото, VIX, UST 10Y), курсы ЕЦБ и ЦБ РФ; FRED по ключу |

Как работает сбор:

* Все источники одного запроса опрашиваются **параллельно**: на каждый 8 с, на весь сбор 15 с.
  Медленные отменяются, а ответ дописывается в кеш для следующих запросов.
* Общий HTTP-клиент с **TTL-кешем** и склейкой одинаковых запросов: если 50 человек
  одновременно спросили BTC, к Binance уйдёт один запрос.
* Ответы приводятся к общим типам (`Quote`, `Market`, `Derivatives`, `DexPair`,
  `NewsItem`, `Metric`, `Security`), а `Snapshot` считает агрегаты: медианную цену,
  спред с отбросом выбросов, средний фандинг, суммарный OI.
* Для **контрактов** CEX по тикеру не опрашиваются: у мемкоина может оказаться
  тикер чужой крупной монеты.

### Как добавить источник

Нужна одна функция в подходящем модуле `bot/sources/providers/`:

```python
@source("mybourse", "MyBourse", category=Category.CEX, scope=Scope.SYMBOL,
        homepage="https://mybourse.com", when=not_quote("USDT"))
async def mybourse(ctx: Ctx):
    d = await ctx.http.get_json("https://api.mybourse.com/ticker", {"pair": f"{ctx.sym}_USDT"})
    return Quote(price=need(d["last"]), change_24h=num(d["change"]), volume_24h=num(d["quoteVol"]))
```

* `scope`: `SYMBOL` — тикер, `CONTRACT` — адрес, `TOKEN` — то или другое,
  `ANY` — с монетой или без (новости), `GLOBAL` — фон рынка.
* Если нужен ключ, передай `key="MYBOURSE_API_KEY"`: источник включится, как только
  переменная появится в `.env`, а сам ключ придёт в `ctx.key`.
* «Нет такой пары» — это `raise NoData(...)` или ответ 400/404, а не ошибка.
* Для источника из планов: замени `planned(...)` на функцию с `@source`.

## Архитектура

```
bot/
  app.py             сборка: настройки, БД, HTTP, реестр, Claude, диспетчер, фоновые слежки
  handlers/          экраны: menu (старт, профиль, помощь), analyze, skills, watches
  keyboards.py texts.py callbacks.py states.py
  query_parser.py    тикеры, кэштеги, контракты, «сравни», «навык X»
  db.py              SQLite: users, usage (запас), skills, watches, queries (тренды)
  services/
    analyzer.py      конвейер: запрос → активы → сбор → отчёт → вывод
    report.py        отчёт (Telegram HTML), сравнение, контекст для ИИ
    llm.py           Claude (claude-opus-5-5, effort medium, серверный fallback при отказе)
    watches.py       разбор условий, проверка, фоновый Watcher
    prices.py quota.py trending.py
  sources/
    base.py          типы данных и DataSource
    registry.py      @source / planned(), выборки по активу
    collector.py     параллельный сбор → Snapshot с агрегатами
    http.py          aiohttp + TTL-кеш + склейка запросов
    resolver.py      тикер → CoinGecko id/имя; контракт → сеть/тикер (DexScreener)
    providers/       cex, derivatives, aggregators, dex, defi, onchain,
                     security, sentiment, news, social, macro
    probe.py         CLI-проверка и генерация каталога
```

Стек: Python 3.11+, aiogram 3, aiohttp, aiosqlite, pydantic-settings, Anthropic SDK.

## Тесты

```bash
pip install pytest pytest-asyncio ruff
python -m pytest -q
ruff check bot tests
```

104 теста без сети:

* парсеры источников на заготовленных ответах API;
* разбор запросов и условий слежек;
* сбор данных: таймауты, дедлайн, выбросы;
* весь конвейер разбора;
* форма запроса к Claude;
* валидность Telegram-HTML на «злых» входных данных;
* сквозные сценарии через `Dispatcher`: `/start` → экраны → создание навыка → слежка → отчёт.

## Что дальше (идеи)

- [ ] Подключить источники из планов по приоритету: CoinGlass (ликвидации), Glassnode/CryptoQuant, Token Unlocks, Farside (ETF), X/Twitter, Telegram-каналы
- [ ] Слежки по контрактам (цена с DEX) и по метрикам (фандинг, OI, F&G)
- [ ] Свечи и теханализ (RSI, уровни) по OHLCV бирж, графики картинкой
- [ ] Тарифы и оплата (Telegram Stars), рефералка, история разборов
- [ ] Redis для FSM и кеша, Postgres вместо SQLite, вебхуки вместо polling
- [ ] Навык по умолчанию и навыки-шаблоны «из коробки»

⚠️ Бот не даёт финансовых советов. Все решения пользователь принимает сам.
