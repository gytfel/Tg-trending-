"""Проверка контрактов на скам: honeypot, налоги, права владельца, mint/freeze."""

from __future__ import annotations

from bot.sources.base import Category, Ctx, NoData, Scope, Security, num
from bot.sources.chains import EVM_CHAIN_IDS, HONEYPOT_CHAINS
from bot.sources.registry import planned, source

SEC = {"category": Category.SECURITY, "scope": Scope.CONTRACT}

GOPLUS_EVM_FLAGS: list[tuple[str, str, str]] = [
    # (поле, плохое значение, описание)
    ("is_honeypot", "1", "honeypot — продать нельзя"),
    ("cannot_sell_all", "1", "нельзя продать весь баланс"),
    ("owner_change_balance", "1", "владелец может менять балансы"),
    ("hidden_owner", "1", "скрытый владелец"),
    ("can_take_back_ownership", "1", "владение можно вернуть"),
    ("is_open_source", "0", "код не верифицирован"),
    ("is_proxy", "1", "прокси-контракт: логику можно подменить"),
    ("is_mintable", "1", "владелец может допечатывать токены"),
    ("transfer_pausable", "1", "переводы можно поставить на паузу"),
    ("is_blacklisted", "1", "есть чёрный список адресов"),
    ("slippage_modifiable", "1", "налоги можно менять"),
    ("personal_slippage_modifiable", "1", "индивидуальные налоги для адресов"),
    ("trading_cooldown", "1", "кулдаун между сделками"),
    ("external_call", "1", "внешние вызовы в контракте"),
]
CRITICAL = {"is_honeypot", "cannot_sell_all", "owner_change_balance", "hidden_owner"}


def _risk(critical: bool, flags: int) -> str:
    if critical:
        return "high"
    if flags >= 2:
        return "medium"
    return "low"


def _goplus_evm(t: dict) -> Security:
    flags: list[str] = []
    critical = False
    for field, bad, text in GOPLUS_EVM_FLAGS:
        if str(t.get(field, "")) == bad:
            flags.append(text)
            critical |= field in CRITICAL
    buy, sell = num(t.get("buy_tax")), num(t.get("sell_tax"))
    buy = buy * 100 if buy is not None else None
    sell = sell * 100 if sell is not None else None
    if sell is not None and sell >= 10:
        flags.append(f"налог на продажу {sell:.0f}%")
        critical |= sell >= 30
    if buy is not None and buy >= 10:
        flags.append(f"налог на покупку {buy:.0f}%")
    owner_share = max(num(t.get("owner_percent")) or 0, num(t.get("creator_percent")) or 0)
    if owner_share >= 0.05:
        flags.append(f"у создателя/владельца {owner_share * 100:.0f}% предложения")
    lp = t.get("lp_holders") or []
    if lp:
        locked = sum(num(h.get("percent")) or 0 for h in lp if str(h.get("is_locked")) == "1")
        if locked < 0.5:
            flags.append(f"залочено {locked * 100:.0f}% ликвидности")
    holders = t.get("holder_count")
    return Security(
        risk=_risk(critical, len(flags)),
        flags=flags,
        buy_tax=buy,
        sell_tax=sell,
        holders=int(holders) if holders else None,
    )


def _status(v: object) -> str:
    if isinstance(v, dict):
        return str(v.get("status", ""))
    return str(v or "")


def _goplus_solana(t: dict) -> Security:
    flags: list[str] = []
    critical = False
    if _status(t.get("mintable")) == "1":
        flags.append("mint authority не отозван — могут допечатать")
    if _status(t.get("freezable")) == "1":
        flags.append("freeze authority — могут заморозить токены")
        critical = True
    if _status(t.get("balance_mutable_authority")) == "1":
        flags.append("балансы можно менять")
        critical = True
    if _status(t.get("closable")) == "1":
        flags.append("токен-аккаунты можно закрыть")
    if str(t.get("non_transferable", "")) == "1":
        flags.append("токен нельзя переводить")
        critical = True
    if t.get("transfer_hook"):
        flags.append("есть transfer hook")
    if _status(t.get("metadata_mutable")) == "1":
        flags.append("метаданные можно менять")
    holders = t.get("holder_count")
    return Security(risk=_risk(critical, len(flags)), flags=flags, holders=int(holders) if holders else None)


@source(
    "goplus",
    "GoPlus Security",
    homepage="https://gopluslabs.io",
    when=lambda a: a.is_solana or (a.chain in EVM_CHAIN_IDS),
    **SEC,
)
async def goplus(ctx: Ctx):
    a = ctx.asset
    addr = ctx.contract
    if a and a.is_solana:
        d = await ctx.http.get_json(
            "https://api.gopluslabs.io/api/v1/solana/token_security",
            {"contract_addresses": addr},
            ttl=600,
        )
        t = (d.get("result") or {}).get(addr)
        if not t:
            raise NoData("нет отчёта")
        return _goplus_solana(t)
    chain_id = EVM_CHAIN_IDS[a.chain]  # type: ignore[index, union-attr]
    d = await ctx.http.get_json(
        f"https://api.gopluslabs.io/api/v1/token_security/{chain_id}",
        {"contract_addresses": addr},
        ttl=600,
    )
    result = d.get("result") or {}
    t = result.get(addr.lower()) or result.get(addr)
    if not t:
        raise NoData("нет отчёта")
    return _goplus_evm(t)


HONEYPOT_RISK = {"honeypot": "high", "high": "high", "medium": "medium", "low": "low", "very_low": "low"}


@source(
    "honeypot_is",
    "Honeypot.is",
    homepage="https://honeypot.is",
    when=lambda a: a.chain in HONEYPOT_CHAINS,
    **SEC,
)
async def honeypot_is(ctx: Ctx):
    chain_id = EVM_CHAIN_IDS[ctx.asset.chain]  # type: ignore[index, union-attr]
    d = await ctx.http.get_json(
        "https://api.honeypot.is/v2/IsHoneypot",
        {"address": ctx.contract, "chainID": chain_id},
        ttl=600,
    )
    flags: list[str] = []
    if (d.get("honeypotResult") or {}).get("isHoneypot"):
        flags.append("симуляция: honeypot")
    sim = d.get("simulationResult") or {}
    buy, sell = num(sim.get("buyTax")), num(sim.get("sellTax"))
    if sell is not None and sell >= 10:
        flags.append(f"налог на продажу {sell:.0f}%")
    for f in (d.get("summary") or {}).get("flags") or []:
        desc = f.get("description") if isinstance(f, dict) else str(f)
        if desc and desc not in flags:
            flags.append(desc)
    risk = HONEYPOT_RISK.get(str((d.get("summary") or {}).get("risk", "")).lower(), "unknown")
    holders = (d.get("token") or {}).get("totalHolders")
    return Security(risk=risk, flags=flags[:6], buy_tax=buy, sell_tax=sell, holders=holders)


@source(
    "rugcheck",
    "RugCheck",
    homepage="https://rugcheck.xyz",
    when=lambda a: a.is_solana,
    **SEC,
)
async def rugcheck(ctx: Ctx):
    d = await ctx.http.get_json(f"https://api.rugcheck.xyz/v1/tokens/{ctx.contract}/report/summary", ttl=600)
    flags: list[str] = []
    danger = warn = 0
    for r in d.get("risks") or []:
        level = r.get("level")
        name = r.get("name") or ""
        if level == "danger":
            danger += 1
            flags.append(f"🔴 {name}")
        elif level == "warn":
            warn += 1
            flags.append(f"🟠 {name}")
    risk = "high" if danger else ("medium" if warn else "low")
    score = num(d.get("score_normalised"))
    return Security(risk=risk, flags=flags[:6], score=score)


# --- в каталоге, ещё не подключены ---------------------------------------------
planned("tokensniffer", "Token Sniffer", Category.SECURITY, "https://tokensniffer.com", key="TOKENSNIFFER_API_KEY")
planned("defi_scanner", "De.Fi Scanner", Category.SECURITY, "https://de.fi/scanner", key="DEFI_API_KEY")
planned("quickintel", "Quick Intel", Category.SECURITY, "https://quickintel.io", key="QUICKINTEL_API_KEY")
planned("solsniffer", "SolSniffer", Category.SECURITY, "https://solsniffer.com", key="SOLSNIFFER_API_KEY")
planned("certik", "CertiK Skynet", Category.SECURITY, "https://skynet.certik.com", key="CERTIK_API_KEY")
planned(
    "chainabuse",
    "Chainabuse",
    Category.SECURITY,
    "https://www.chainabuse.com",
    key="CHAINABUSE_API_KEY",
    note="жалобы на адреса",
)
