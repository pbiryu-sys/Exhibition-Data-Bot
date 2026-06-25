# Exhibition Procurement Bot

Telegram bot that collects product data from exhibitions and returns formatted price cards with calculated landed cost and retail price.

## Run & Operate

- `cd artifacts/tg-bot && python3 bot.py` — run the Telegram bot (managed via "Telegram Bot" workflow)
- Required secret: `TELEGRAM_BOT_TOKEN` — from @BotFather on Telegram

## Stack

- Python 3.12
- python-telegram-bot 20.7 (async, ConversationHandler)
- Settings stored in `artifacts/tg-bot/settings.json` (editable at runtime via bot)

## Where things live

- `artifacts/tg-bot/bot.py` — main bot logic, conversation handlers
- `artifacts/tg-bot/pricing.py` — pricing engine (COST and SELL_PRICE formulas)
- `artifacts/tg-bot/formatters.py` — card formatting and settings display
- `artifacts/tg-bot/states.py` — ConversationHandler state enums
- `artifacts/tg-bot/settings.json` — runtime-editable pricing parameters

## Architecture decisions

- Settings are stored in a JSON file and loaded fresh on every card calculation — changing margin/VAT/rates takes effect immediately, no restart needed.
- ConversationHandler per feature (product entry, settings) with `allow_reentry=True` so users can restart mid-flow.
- Photo is optional (via /skip) and sent as caption when present.
- All pricing params are dynamic; nothing is hardcoded in formulas.

## Product

- Step-by-step 7-step flow: photo → factory → category → article → FOB price → 40HQ qty → note
- Outputs formatted card: FOB in USD + RUB, logistics/customs breakdown per unit, landed cost, retail price, factory tag + year
- Settings panel to adjust: margin, VAT, exchange rate, container cost, customs %, broker/terminal/storage/forwarder fees

## Pricing formula

```
per_unit_logistics = (container_cost + broker_fee + terminal_fee + storage_fee + forwarder_fee + customs_total) / qty_40hq
COST = fob_usd * exchange_rate + per_unit_logistics
SELL_PRICE = COST / (1 - margin) * vat
```

## User preferences

_Populate as you build — explicit user instructions worth remembering across sessions._

## Gotchas

- python-telegram-bot 20.x is fully async; do not mix sync handlers
- settings.json is read on every calculation — no caching needed
- `pnpm-workspace` and Node.js artifacts coexist; bot is pure Python in `artifacts/tg-bot/`
