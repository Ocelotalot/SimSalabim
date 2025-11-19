1. Назначение и цели проекта

Интрадей-бот для линейных USDT-фьючерсов Bybit (категория USDT perpetual), работающий 24/7, с:

управляемым риском (virtual equity, per-trade risk, дневные лимиты, max позиций),

набором модульных стратегий А–E,

фильтрами ликвидности, спреда, rotation-score, slippage,

режимами demo (testnet) / live (mainnet),

Telegram-интерфейсом управления и уведомлений,

сбором подробной статистики (PnL, Leakage, execution costs) и логированием.

Пользователь не пишет код; реализация делается через отдельный чат с инструментом вроде Codex, но в этом документе он не фигурирует — только сама архитектура и требования к коду.

2. Функциональные требования
2.1. Режим работы и управление запуском

Бот запускается локально (macOS, Python ≥ 3.11).

Работает в одном экземпляре.

Основной режим — круглосуточный (24/7), с циклом обработки каждые update_interval_sec секунд.

Управление:

/start_bot — установить флаг «бот запущен» и запустить торговый цикл;

/stop_bot — установить флаг «бот остановлен»; цикл продолжает жить, но новые торговые итерации не исполняет, пока флаг не изменится обратно.

При старте/перезапуске процесса:

бот читает конфиги и runtime-состояние,

обращается к Bybit и подтягивает активные позиции по текущему режиму (demo или live),

восстанавливает из них PositionState,

учитывает их в лимите max_concurrent_positions.

2.2. Demo / Live

Есть два набора API-ключей (demo/testnet и live/mainnet).

В конфиге торговли есть параметр bybit_mode: "demo" | "live".

Все вызовы к Bybit (data feed, ордера, позиции) используют bybit_mode + соответствующие ключи.

Переключение между demo/live делается изменением конфигурации (и перезапуском процесса или явной перезагрузкой конфигов).

2.3. Торговый универсум и Rotation

Список торгуемых инструментов описан в symbols.yml:

symbol, group (core, plus, rotation),

enabled: bool,

лимиты по плечу и notional.

Флаг enabled: false — жёсткий запрет на новые входы по символу, независимо от rotation-score.

Подсистема Rotation:

периодически вычисляет score [0..1] по каждому символу на основании:

ликвидности (объём, глубина стакана),

среднего спреда,

недавнего PnL,

качества исполнения (slippage, execution costs),

хранит RotationState (набор SymbolScore + список символов, разрешённых для новых входов).

В конфиге Rotation минимум:

enabled: bool,

min_score_for_new_entry: float,

max_active_symbols: int.

Rotation управляет только разрешением новых входов:

активные позиции по символу могут продолжать жить даже при падении score;

при расчёте разрешённых символов учитываются:

enabled == true,

score >= min_score_for_new_entry,

ограничение по max_active_symbols.

2.4. Режим рынка и профили таймфреймов

На основе индикаторов (ATR, ADX, диапазон, волатильность) определяется режим:

Regime.TREND,

Regime.RANGE.

На основе режима и времени суток выбирается TF-профиль:

TfProfile.AGGR, BAL, CONS,

после 22:00 (по заданной таймзоне) профиль может смещаться в более консервативный (регламентируется в логике tf_selector и/или конфиге).

Режим и TF-профиль входят в MarketState и используются стратегиями и фильтрами.

2.5. Управление рисками и лимитами

Есть понятие виртуального депозита: virtual_equity_usdt.

Задаётся в конфиге и не пересчитывается автоматически по факту торговли.

Используется для:

расчёта размера позиции (per-trade риск),

дневных лимитов потерь в процентах.

Может быть изменён через Telegram (/set_equity).

Параметры риска:

per_trade_risk_pct — риск на сделку в процентах от virtual_equity_usdt.

daily_max_loss_pct — дневной лимит потерь (от virtual equity).

max_concurrent_positions — максимальное количество одновременных открытых позиций.

лимиты по ожидаемому/фактическому slippage в bps.

Политика:

размер позиции рассчитывается так, чтобы риск до SL не превышал per_trade_risk_pct * virtual_equity_usdt;

если потенциальная сделка нарушает лимиты (risk, дневной PnL, max позиций) — вход запрещается;

есть «пул позиций» общий для всех стратегий.

2.6. Пул позиций и конфликт сигналов

Для каждого символа в каждый момент времени — одна net-позиция:

FLAT, LONG, SHORT.

Внутри позиции — одна или несколько legs (PositionLeg) от разных стратегий:

каждая leg хранит свой объём, цену входа, идентификатор стратегии и метаданные.

Вход в ту же сторону по тому же символу:

разрешён (pyramiding), если:

не превышается max_concurrent_positions,

соблюдены лимиты риска и дневного PnL.

Вход в противоположную сторону по тому же символу:

запрещён, пока текущая позиция не будет закрыта (для разворота позиция сначала закрывается, затем может открываться в другую сторону).

Конфликт сигналов по одному символу (например, A → long, B → short):

у каждой стратегии есть priority: int (в strategies.yml);

при конфликте выбирается сигнал стратегии с наивысшим приоритетом (наименьшее число);

остальные сигналы по этому символу в данном цикле помечаются как skipped_due_to_conflict и логируются.

2.7. Стратегии A–E и расширяемость

Все стратегии реализуют единый интерфейс BaseStrategy:

свойства id: str, name: str;

метод generate_signals(market_state: dict[str, MarketState], position_state: dict[str, PositionState], config: StrategyRuntimeConfig) -> list[Signal].

Каждая стратегия:

анализирует MarketState, режим, TF-профиль, свои параметры;

учитывает текущее состояние позиций (например, не даёт повторный сигнал, если логика этого не допускает);

возвращает список сигналов Signal.

Signal содержит:

id, strategy_id, symbol, side,

entry_type ("limit_on_retest", "market_with_cap"),

entry_level (уровень пробоя/ретеста),

sl_price, tp_levels (список уровней частичного выхода),

time_stop_bars и др. параметры.

Реестр стратегий:

STRATEGY_REGISTRY: dict[str, type[BaseStrategy]] в strategies/registry.py.

по strategies.yml создаётся набор активных стратегий (с учётом enabled и priority).

При добавлении новой стратегии:

реализуется новый класс, наследник BaseStrategy,

регистрируется в STRATEGY_REGISTRY,

добавляется конфигурация в strategies.yml;

после этого она автоматически участвует в торговле.

2.8. Execution, limit_on_retest, SL/TP, time-stop, slippage

Execution-слой отвечает за:

первичную синхронизацию позиций с биржей при старте,

управление EntryIntent (pending-входы, в т.ч. limit_on_retest),

конвертацию OrderIntent в реальные ордера на Bybit,

трекинг активных ордеров и обновление PositionState,

управление SL/TP/time-stop и частичными закрытиями.

limit_on_retest (выбран первый вариант реализации):

стратегия создаёт EntryIntent со значениями:

symbol, side,

trigger_level,

expiry_bars,

planned_qty,

entry_type="limit_on_retest".

ExecutionEngine отслеживает рынок:

при условии ретеста (для long: low <= trigger_level <= high на баре) выставляет лимитный ордер около trigger_level (возможен post_only);

у ордера есть TTL; неисполненный остаток снимается по TTL.

Частичное исполнение:

исполненная часть становится частью реальной позиции;

оставшийся объём отменяется.

market_with_cap:

ордер по рынку, с предварительной оценкой ожидаемого slippage по стакану;

если ожидаемое expected_slippage_bps выше порога — сделка может быть отклонена до отправки.

Политика по проска́льзыванию:

pre-trade:

оценивается expected_slippage_bps по стакану;

если выше max_expected_slippage_bps, вход не допускается.

post-trade:

для каждого исполнения считается actual_slippage_bps;

если превышает max_actual_slippage_bps, событие логируется как риск;

при серии таких событий может активироваться защитный механизм (kill-switch, временная остановка новых сделок).

для выходов (SL/TP/time-stop):

сделка всегда выполняется (slippage не блокирует закрытие),

slippage учитывается только в статистике и фактическом PnL.

SL/TP и частичные закрытия:

реализуются как управляемые рыночные/лимитные операции:

по достижении уровней TP1/TP2 ExecutionEngine отправляет ордера с расчётным объёмом части позиции (в процентах от текущего объёма),

остаток сопровождается по trailing-логике и time-stop.

Time-stop:

после заданного числа баров (time_stop_bars) с момента открытия позиция закрывается принудительно (обычно по рынку).

2.9. Telemetry, PnL и Leakage

Для каждой закрытой позиции формируется TradeRecord с:

symbol, strategy_id,

entry_time, exit_time,

entry_price, exit_price,

qty,

gross_pnl, net_pnl,

execution_costs_abs (комиссии + slippage),

traded_notional_usdt (суммарный объём в деньгах).

На уровне сессии/дня считается SessionStats:

gross_pnl, net_pnl,

execution_costs_abs,

traded_notional_usdt,

leakage_abs = gross_pnl - net_pnl,

leakage_pct и leakage_valid по политике:

если gross_pnl > 0:

leakage_pct = leakage_abs / gross_pnl * 100,

leakage_valid = true;

если gross_pnl <= 0:

leakage_pct = null,

leakage_valid = false.

execution_costs_pct_of_notional = execution_costs_abs / traded_notional_usdt * 100.

Telemetry отвечает за:

структурированное логирование событий (JSON-логи),

запись трейдов (CSV/Parquet) в файловую систему,

сохранение SessionStats в runtime/session_stats.json,

при необходимости — отправку кратких отчётов/сводок в Telegram.

2.10. Telegram-интерфейс

Взаимодействие с пользователем происходит через Telegram-бота:

уведомления:

открытие/закрытие позиций (краткий текст),

срабатывание SL/TP/time-stop,

ошибки/kill-switch,

периодические или дневные краткие отчёты (по потребности).

команды управления:

/start_bot — установить флаг bot_running = true в runtime-состоянии;

/stop_bot — bot_running = false;

/status — вывести:

режим (demo/live),

virtual equity,

per-trade риск,

max позиций,

количество и суммарный PnL текущих позиций (агрегированно),

упрощённую информацию по SessionStats;

/set_risk <percent> — обновить per_trade_risk_pct в runtime;

/set_equity <value> — обновить virtual_equity_usdt в runtime;

/set_max_positions <value> — обновить max_concurrent_positions в runtime.

Telegram-слой читает токен из credentials.yml и текущие параметры из runtime/runtime_state.json.

Архитектура позволяет расширить набор команд (например, ручное отключение/включение стратегии, изменение порогов slippage и пр.) без изменений в core-логике.

3. Архитектура верхнего уровня
3.1. Подсистемы

config — конфиги и их валидация.

core — общие типы, перечисления, ошибки, утилиты времени.

data_feed — подключение к Bybit (REST/WebSocket), получение свечей, стакана, трейдов.

market:

модели рыночных данных,

расчёт индикаторов,

построение MarketState,

классификация режима и TF-профиля,

базовые фильтры (ликвидность, спред, rotation).

strategies — набор стратегий, общий интерфейс, реестр.

risk — учёт позиций и движка риска.

execution — слой исполнения ордеров, EntryIntent, sync с биржей.

rotation — оценка symbol-score и отбор символов.

telemetry — события, трейды, статистика, логи.

interfaces — Telegram-бот (и в перспективе веб-интерфейс).

runtime — файлы состояния, доступные между перезапусками.

main — точка входа и главный цикл.

3.2. Поток данных в основном цикле

Каждую итерацию (если bot_running = true):

Чтение runtime-состояния (обновлённые параметры риска и флаг bot_running).

DataFeed → Market:

получение сырых данных по всем активным символам,

обновление историй (свечи, трейды),

расчёт индикаторов (через TA-библиотеку),

построение MarketState по каждому символу.

Market → Regime/TF/Filters:

классификация режима (Trend/Range),

выбор TF-профиля,

применение фильтров ликвидности и спреда,

применение Rotation-фильтра (по RotationState).

Strategies:

для каждой включённой стратегии:

вызов generate_signals(...),

получение списка Signal.

Risk:

RiskEngine собирает все сигналы,

разрешает/отклоняет их с учётом:

лимитов риска,

текущих позиций и дневного PnL,

конфликта сигналов (приоритезация по стратегии),

рассчитывает объём и формирует OrderIntent/EntryIntent.

Execution:

ExecutionEngine:

синхронизирует активные ордера,

обрабатывает EntryIntent (особенно limit_on_retest),

отправляет новые ордера,

обновляет PositionState по ExecutionReports.

Telemetry:

фиксируются события, трейды,

периодически пересчитывается SessionStats,

при необходимости отправляются уведомления в Telegram.

Rotation:

по накопленным данным и статистике обновляется RotationState,

результат используется фильтрами на следующих итерациях.

4. Структура проекта

Предлагаемая структура каталогов:

/app
  __init__.py
  main.py

  /config
    __init__.py
    models.py          # Pydantic-модели конфигов
    loader.py          # функции load_*_config
    trading.yml        # торговые настройки (risk, rotation, обновление и т.п.)
    symbols.yml        # список символов и лимитов
    strategies.yml     # стратегии, их параметры, enabled/priority
    credentials.example.yml

  /core
    __init__.py
    enums.py           # Side, OrderType, TimeInForce, Regime, TfProfile, EntryType, StrategyId
    types.py           # общие алиасы типов
    time_utils.py      # таймзоны, текущее время
    errors.py          # иерархия ошибок

  /data_feed
    __init__.py
    bybit_client.py    # подключение к Bybit (demo/live)
    candles.py         # работа со свечами
    orderbook.py       # стакан, spread, liquidity
    trades.py          # треки, buy/sell volume, delta_flow

  /market
    __init__.py
    models.py          # Candle, OrderBookSnapshot, TradeTick, IndicatorState, MarketState
    indicators.py      # адаптер к TA-библиотеке
    market_state_builder.py
    regime_classifier.py
    tf_selector.py
    filters.py         # ликвидность, спред, rotation

  /strategies
    __init__.py
    base.py            # BaseStrategy, Signal
    registry.py        # STRATEGY_REGISTRY
    strategy_a_trend_continuation.py
    strategy_b_bb_squeeze.py
    strategy_c_range_break.py
    strategy_d_vwap_mean_reversion.py
    strategy_e_liquidity_sweep.py

  /risk
    __init__.py
    models.py          # PositionLeg, PositionState, RiskLimits, RiskDecision
    risk_engine.py

  /execution
    __init__.py
    models.py          # EntryIntent, OrderIntent, ActiveOrder, ExecutionReport
    execution_engine.py
    sync_state.py      # восстановление позиций при старте

  /rotation
    __init__.py
    models.py          # SymbolScore, RotationState
    rotation_engine.py

  /telemetry
    __init__.py
    events.py          # TelemetryEvent, TradeRecord, SessionStats
    storage.py         # запись трейдов, сессий
    logging_setup.py   # настройка логгера

  /interfaces
    __init__.py
    telegram_bot.py    # Telegram-интерфейс и команды

  /runtime
    runtime_state.json     # текущие параметры риска, флаг bot_running
    session_stats.json     # последняя сессионная статистика

/data
  trades/                 # CSV/Parquet с трейдами
  logs/                   # логи (при необходимости)

/tests
  ... (тесты)

TZ.txt
README.md
.gitignore
requirements.txt

5. Модели данных (сводно по подсистемам)

Ниже — краткий перечень ключевых моделей, которые должны быть реализованы (в коде можно выбрать dataclass или Pydantic, где удобнее).

5.1. Config

ApiCredentialsConfig

SymbolConfig

StrategyRuntimeConfig

RiskConfig

RotationConfig

TradingConfig

AppConfig

5.2. Market / Core

Candle, OrderBookSnapshot, TradeTick, IndicatorState, MarketState.

Enum: Side, OrderType, TimeInForce, Regime, TfProfile, EntryType, StrategyId.

Вспомогательные типы (Numeric, Timestamp и т.п.).

5.3. Strategies / Risk / Execution

Signal (strategies.base).

PositionLeg, PositionState, RiskLimits, RiskDecision.

EntryIntent, OrderIntent, ActiveOrder, ExecutionReport.

5.4. Rotation / Telemetry / Runtime

SymbolScore, RotationState.

TelemetryEvent, TradeRecord, SessionStats.

Структура runtime-JSON:

bot_running: bool,

per_trade_risk_pct: float,

virtual_equity_usdt: float,

max_concurrent_positions: int,

при необходимости — доп. поля (например, текущий дневной PnL, дата начала сессии).

6. Тестирование (что должно быть покрыто хотя бы минимально)

Минимум:

config.loader — корректная загрузка и валидация конфигов.

risk_engine:

расчёт размера позиции при заданном SL,

соблюдение max_concurrent_positions,

блокировка в случае превышения дневного лимита потерь.

market.filters:

фильтр ликвидности и спреда,

корректная работа rotation-фильтра.

rotation_engine:

формирование score и отбор символов по порогам.

Логика конфликтов сигналов и приоритета стратегий.