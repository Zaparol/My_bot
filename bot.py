import json
import os
import asyncio
import logging
from ta.trend import ADXIndicator, SMAIndicator, EMAIndicator, MACD
from ta.volatility import BollingerBands, AverageTrueRange
from ta.momentum import RSIIndicator, StochasticOscillator
from ta.volume import OnBalanceVolumeIndicator, MFIIndicator
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
)
from datetime import datetime, timedelta, timezone
from binance import Client
from binance.exceptions import BinanceAPIException
import pandas as pd

# ======================= Конфигурация =======================

# Замените на ваш реальный Telegram API токен
API_TOKEN = '7564004976:AAF2YlriNyPWFh964GYoy__Wepcp-2RZNDI'

# Замените на ваши Binance API ключ и секрет (если требуется)
BINANCE_API_KEY = 'YOUR_BINANCE_API_KEY'
BINANCE_API_SECRET = 'YOUR_BINANCE_API_SECRET'

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.INFO  # Уровень логирования: DEBUG, INFO, WARNING, ERROR, CRITICAL
)
logger = logging.getLogger(__name__)

# Настройка уровней логирования для внешних библиотек
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
logging.getLogger("apscheduler").setLevel(logging.WARNING)
logging.getLogger("binance").setLevel(logging.WARNING)

# Конфигурация
CRYPTO_SYMBOLS = ['BTCUSDT', 'TONUSDT', 'PEPEUSDT', 'SOLUSDT', 'NEARUSDT', 'ETHUSDT', 'DOGEUSDT']
SUBSCRIBERS_FILE = 'subscribers.json'
DATA_DIR = 'historical_data'  # Директория для хранения исторических данных

# Таймфреймы для мультивременного анализа
TIMEFRAMES = ['15m', '30m', '1h', '1d']  # 5 минут, 15 минут, 1 час и 1 день

# Словарь для кэширования трендов
market_trends = {}

# ======================= Инициализация Binance Client =======================

try:
    client = Client(BINANCE_API_KEY, BINANCE_API_SECRET)
    client.ping()  # Проверяем соединение
    logger.info("✅ Успешно подключились к Binance API.")
except BinanceAPIException as e:
    logger.error(f"❌ Ошибка подключения к Binance API: {e}")
    exit(1)
except Exception as e:
    logger.error(f"❌ Неизвестная ошибка при подключении к Binance API: {e}")
    exit(1)

# Создаём директорию для хранения данных, если её нет
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)
    logger.info(f"📁 Создана директория для хранения данных: {DATA_DIR}")

# ======================= Функции для работы с подписчиками =======================

def load_subscribers():
    """Загружает список подписчиков из файла."""
    if not os.path.exists(SUBSCRIBERS_FILE):
        with open(SUBSCRIBERS_FILE, 'w') as f:
            json.dump([], f)
        logger.info(f"📄 Создан новый файл {SUBSCRIBERS_FILE} для хранения подписчиков.")
        return set()

    try:
        with open(SUBSCRIBERS_FILE, 'r') as f:
            data = f.read().strip()
            if not data:
                logger.info(f"📄 Файл {SUBSCRIBERS_FILE} пуст.")
                return set()
            subscribers = set(json.loads(data))
            logger.info(f"👥 Загружено {len(subscribers)} подписчиков.")
            return subscribers
    except json.JSONDecodeError:
        logger.error(f"❌ Некорректный формат JSON в файле {SUBSCRIBERS_FILE}. Сброс файла.")
        with open(SUBSCRIBERS_FILE, 'w') as f:
            json.dump([], f)
        return set()
    except Exception as e:
        logger.error(f"❌ Ошибка при загрузке подписчиков: {e}")
        return set()

def save_subscribers(subscribers):
    """Сохраняет список подписчиков в файл."""
    try:
        with open(SUBSCRIBERS_FILE, 'w') as f:
            json.dump(list(subscribers), f)
        logger.info(f"💾 Сохранено {len(subscribers)} подписчиков.")
    except Exception as e:
        logger.error(f"❌ Ошибка при сохранении подписчиков: {e}")

# ======================= Функции для загрузки и обновления исторических данных =======================

def get_historical_data_file(symbol, timeframe):
    """Возвращает имя файла для хранения исторических данных конкретной криптовалюты и таймфрейма."""
    return os.path.join(DATA_DIR, f"{symbol}_{timeframe}_historical_data.csv")

def load_historical_data(symbol, timeframe):
    """Загружает исторические данные из файла или Binance API."""
    HISTORICAL_DATA_FILE = get_historical_data_file(symbol, timeframe)
    if not os.path.exists(HISTORICAL_DATA_FILE):
        logger.info(f"📊 Файл {HISTORICAL_DATA_FILE} не найден. Загружаю данные с Binance для {symbol} на таймфрейме {timeframe}...")
        try:
            # Определяем количество дней для исторических данных в зависимости от таймфрейма
            if timeframe == '1d':
                days = 500
            elif timeframe == '1h':
                days = 200
            elif timeframe == '30m':
                days = 200
            elif timeframe == '15m':
                days = 200
            else:
                days = 200  # По умолчанию 30 дней

            start_time = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000)
            klines = client.get_historical_klines(
                symbol,
                timeframe,
                start_str=start_time,
                limit=1000
            )
            df = pd.DataFrame(klines, columns=[
                'timestamp', 'open', 'high', 'low', 'close', 'volume',
                'close_time', 'quote_asset_volume', 'number_of_trades',
                'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'
            ])
            # Конвертация временных меток и установка индекса с временной зоной UTC
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
            df.set_index('timestamp', inplace=True)
            # Конвертация цен в числовые значения
            for col in ['open', 'high', 'low', 'close', 'volume']:
                df[col] = pd.to_numeric(df[col], errors='coerce')
            df.to_csv(HISTORICAL_DATA_FILE)
            logger.info(f"📁 Исторические данные для {symbol} на таймфрейме {timeframe} сохранены в {HISTORICAL_DATA_FILE}.")
            return df
        except BinanceAPIException as e:
            logger.error(f"❌ Ошибка при запросе к Binance API для {symbol} на таймфрейме {timeframe}: {e}")
            return pd.DataFrame()
        except Exception as e:
            logger.error(f"❌ Неизвестная ошибка при загрузке данных для {symbol} на таймфрейме {timeframe}: {e}")
            return pd.DataFrame()
    else:
        logger.info(f"📊 Загружаю исторические данные из {HISTORICAL_DATA_FILE} для {symbol} на таймфрейме {timeframe}...")
        try:
            df = pd.read_csv(
                HISTORICAL_DATA_FILE,
                index_col='timestamp',
                parse_dates=True
            )
            logger.info(f"📁 Исторические данные для {symbol} на таймфрейме {timeframe} успешно загружены из {HISTORICAL_DATA_FILE}.")
            return df
        except Exception as e:
            logger.error(f"❌ Ошибка при загрузке исторических данных для {symbol} на таймфрейме {timeframe}: {e}")
            return pd.DataFrame()

def update_historical_data(df, symbol, timeframe):
    """Обновляет исторические данные, загружая новые записи с Binance API."""
    HISTORICAL_DATA_FILE = get_historical_data_file(symbol, timeframe)
    if df.empty:
        logger.info(f"📉 DataFrame пуст для {symbol} на таймфрейме {timeframe}. Загружаю данные заново.")
        return load_historical_data(symbol, timeframe)
    
    last_timestamp = df.index[-1]
    now = datetime.now(timezone.utc)
    delta = now - last_timestamp
    # Определяем интервал обновления в зависимости от таймфрейма
    if timeframe.endswith('m'):
        interval_minutes = int(timeframe.replace('m', ''))
        interval_delta = timedelta(minutes=interval_minutes)
    elif timeframe.endswith('h'):
        interval_hours = int(timeframe.replace('h', ''))
        interval_delta = timedelta(hours=interval_hours)
    elif timeframe.endswith('d'):
        interval_days = int(timeframe.replace('d', ''))
        interval_delta = timedelta(days=interval_days)
    else:
        interval_delta = timedelta(minutes=1)  # По умолчанию 1 минута

    # Обновляем данные независимо от формирования новой свечи
    logger.info(f"📈 Обновляю исторические данные для {symbol} на таймфрейме {timeframe}...")
    try:
        # Преобразуем last_timestamp в миллисекунды
        start_time = int(last_timestamp.timestamp() * 1000) + 1  # Добавляем 1 мс, чтобы не дублировать последнюю запись
        klines = client.get_historical_klines(
            symbol,
            timeframe,
            start_str=start_time,
            limit=1000
        )
        if not klines:
            logger.info(f"🔄 Нет новых данных для обновления {symbol} на таймфрейме {timeframe}.")
            return df
        new_df = pd.DataFrame(klines, columns=[
            'timestamp', 'open', 'high', 'low', 'close', 'volume',
            'close_time', 'quote_asset_volume', 'number_of_trades',
            'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'
        ])
        # Конвертация временных меток и установка индекса с временной зоной UTC
        new_df['timestamp'] = pd.to_datetime(new_df['timestamp'], unit='ms', utc=True)
        new_df.set_index('timestamp', inplace=True)
        # Конвертация цен в числовые значения
        for col in ['open', 'high', 'low', 'close', 'volume']:
            new_df[col] = pd.to_numeric(new_df[col], errors='coerce')
        # Объединяем существующие данные с новыми
        df = pd.concat([df, new_df]).drop_duplicates().sort_index()
        df.to_csv(HISTORICAL_DATA_FILE)
        logger.info(f"📁 Исторические данные для {symbol} на таймфрейме {timeframe} обновлены.")
    except BinanceAPIException as e:
        logger.error(f"❌ Ошибка при обновлении данных с Binance API для {symbol} на таймфрейме {timeframe}: {e}")
    except Exception as e:
        logger.error(f"❌ Неизвестная ошибка при обновлении данных для {symbol} на таймфрейме {timeframe}: {e}")
    return df

def get_crypto_data(df, symbol, timeframe):
    """Получает и обновляет данные о криптовалюте."""
    df = update_historical_data(df, symbol, timeframe)
    logger.info(f"📈 Текущее количество записей для {symbol} на таймфрейме {timeframe}: {len(df)}")
    return df

# ======================= Функция для определения общего тренда =======================

def get_market_trend(symbol):
    """Определяет общий тренд рынка для заданного символа на высоком таймфрейме."""
    timeframe = '1h'  # Можно изменить на '1d' для ещё более высокого таймфрейма
    global market_trends

    # Проверяем, нужно ли обновлять тренд
    last_update = market_trends.get(symbol, {}).get('last_update')
    if last_update:
        now = datetime.now(timezone.utc)
        time_since_update = now - last_update
        interval_hours = int(timeframe.replace('h', ''))
        if time_since_update < timedelta(hours=interval_hours):
            # Тренд актуален, возвращаем сохранённый
            return market_trends[symbol]['trend']

    df = load_historical_data(symbol, timeframe)
    df = get_crypto_data(df, symbol, timeframe)
    
    if df.empty or len(df) < 200:
        logger.warning(f"⚠️ Недостаточно данных для определения тренда для {symbol} на таймфрейме {timeframe}.")
        return None  # Не можем определить тренд

    sma50 = SMAIndicator(close=df['close'], window=50).sma_indicator()
    sma200 = SMAIndicator(close=df['close'], window=200).sma_indicator()

    if sma50.iloc[-1] > sma200.iloc[-1]:
        trend = 'uptrend'  # Восходящий тренд
        logger.info(f"📈 Общий тренд для {symbol} на таймфрейме {timeframe}: Восходящий.")
    elif sma50.iloc[-1] < sma200.iloc[-1]:
        trend = 'downtrend'  # Нисходящий тренд
        logger.info(f"📉 Общий тренд для {symbol} на таймфрейме {timeframe}: Нисходящий.")
    else:
        trend = 'sideways'  # Боковой тренд
        logger.info(f"➡️ Общий тренд для {symbol} на таймфрейме {timeframe}: Боковой.")

    # Сохраняем тренд и время обновления
    market_trends[symbol] = {
        'trend': trend,
        'last_update': datetime.now(timezone.utc)
    }
    return trend

# ======================= Функция для анализа данных и генерации сигналов =======================

def analyze_data(df, symbol, timeframe, trend):
    """Анализирует данные и генерирует торговые сигналы на основе технических индикаторов."""
    if df.empty:
        logger.info(f"❌ Нет данных для анализа для {symbol} на таймфрейме {timeframe}.")
        return None

    # Инициализация индикаторов
    try:
        # Скользящие средние
        sma5 = SMAIndicator(close=df['close'], window=5)
        sma10 = SMAIndicator(close=df['close'], window=10)
        df['SMA5'] = sma5.sma_indicator()
        df['SMA10'] = sma10.sma_indicator()

        # EMA для более быстрого реагирования на изменения цены
        ema20 = EMAIndicator(close=df['close'], window=20)
        df['EMA20'] = ema20.ema_indicator()

        # Индекс относительной силы (RSI)
        rsi = RSIIndicator(close=df['close'], window=14)
        df['RSI'] = rsi.rsi()

        # MACD
        macd = MACD(close=df['close'])
        df['MACD'] = macd.macd()
        df['MACD_signal'] = macd.macd_signal()

        # Полосы Боллинджера
        bollinger = BollingerBands(close=df['close'], window=20, window_dev=2)
        df['Bollinger_High'] = bollinger.bollinger_hband()
        df['Bollinger_Low'] = bollinger.bollinger_lband()

        # Стохастик
        stochastic = StochasticOscillator(
            high=df['high'], low=df['low'], close=df['close'], window=14, smooth_window=3)
        df['Stochastic'] = stochastic.stoch()

        # ADX
        adx = ADXIndicator(high=df['high'], low=df['low'], close=df['close'], window=14)
        df['ADX'] = adx.adx()

        # ATR (Average True Range) для оценки волатильности
        atr = AverageTrueRange(high=df['high'], low=df['low'], close=df['close'], window=14)
        df['ATR'] = atr.average_true_range()

        # Индикаторы объема
        obv = OnBalanceVolumeIndicator(close=df['close'], volume=df['volume'])
        df['OBV'] = obv.on_balance_volume()

        mfi = MFIIndicator(high=df['high'], low=df['low'], close=df['close'], volume=df['volume'], window=14)
        df['MFI'] = mfi.money_flow_index()

        # Распознавание свечных паттернов
        df['Candlestick_Pattern'] = df.apply(detect_candlestick_pattern, axis=1)

    except Exception as e:
        logger.error(f"❌ Ошибка при расчёте индикаторов для {symbol} на таймфрейме {timeframe}: {e}")
        return None

    # Проверка наличия достаточных данных
    if df.empty or len(df) < 2:
        logger.info(f"❌ Недостаточно данных для анализа для {symbol} на таймфрейме {timeframe}.")
        return None

    try:
        latest = df.iloc[-1]
        previous = df.iloc[-2]
    except IndexError as e:
        logger.error(f"❌ Недостаточно данных для анализа для {symbol} на таймфрейме {timeframe}: {e}")
        return None

    # Логирование значений индикаторов
    logger.info(
        f"📊 Проверка сигналов для {symbol} на таймфрейме {timeframe}: "
        f"SMA5={latest['SMA5']}, SMA10={latest['SMA10']}, RSI={latest['RSI']}, "
        f"MACD={latest['MACD']}, MACD_signal={latest['MACD_signal']}, "
        f"OBV={latest['OBV']}, MFI={latest['MFI']}, "
        f"Candlestick_Pattern={latest['Candlestick_Pattern']}"
    )

    # Используем ADX как фильтр для тренда
    if latest['ADX'] < 25:
        logger.info(f"⚠️ Тренд не является сильным для {symbol} на таймфрейме {timeframe} (ADX < 25). Сигналы не генерируются.")
        return None

    # Словарь для хранения названий индикаторов
    indicator_names = {
        'SMA': 'Скользящие Средние (SMA)',
        'RSI': 'Индекс Относительной Силы (RSI)',
        'MACD': 'MACD',
        'Bollinger': 'Полосы Боллинджера',
        'Stochastic': 'Стохастик',
        'OBV': 'On-Balance Volume (OBV)',
        'MFI': 'Индекс Денежного Потока (MFI)',
        'Candlestick': f"Свечной Паттерн ({latest['Candlestick_Pattern']})"
    }

    # Создание списков сигналов с названиями индикаторов
    buy_signals = []
    sell_signals = []

    # Проверяем условия для каждого индикатора и добавляем названия индикаторов в списки

    # Исправленные условия для скользящих средних
    if previous['SMA5'] < previous['SMA10'] and latest['SMA5'] >= latest['SMA10']:
        buy_signals.append(indicator_names['SMA'])
    elif previous['SMA5'] > previous['SMA10'] and latest['SMA5'] <= latest['SMA10']:
        sell_signals.append(indicator_names['SMA'])

    # RSI
    if latest['RSI'] < 30:
        buy_signals.append(indicator_names['RSI'])
    elif latest['RSI'] > 70:
        sell_signals.append(indicator_names['RSI'])

    # MACD
    if previous['MACD'] < previous['MACD_signal'] and latest['MACD'] >= latest['MACD_signal']:
        buy_signals.append(indicator_names['MACD'])
    elif previous['MACD'] > previous['MACD_signal'] and latest['MACD'] <= latest['MACD_signal']:
        sell_signals.append(indicator_names['MACD'])

    # Исправленные условия для полос Боллинджера
    if latest['close'] > latest['Bollinger_High']:
        buy_signals.append(indicator_names['Bollinger'])
    elif latest['close'] < latest['Bollinger_Low']:
        sell_signals.append(indicator_names['Bollinger'])

    # Стохастик
    if latest['Stochastic'] < 20:
        buy_signals.append(indicator_names['Stochastic'])
    elif latest['Stochastic'] > 80:
        sell_signals.append(indicator_names['Stochastic'])

    # Индикаторы объема
    if latest['OBV'] > previous['OBV']:
        buy_signals.append(indicator_names['OBV'])
    elif latest['OBV'] < previous['OBV']:
        sell_signals.append(indicator_names['OBV'])

    if latest['MFI'] < 20:
        buy_signals.append(indicator_names['MFI'])
    elif latest['MFI'] > 80:
        sell_signals.append(indicator_names['MFI'])

    # Свечные паттерны
    if latest['Candlestick_Pattern'] in ['Bullish Engulfing', 'Hammer', 'Doji']:
        buy_signals.append(indicator_names['Candlestick'])
    elif latest['Candlestick_Pattern'] in ['Bearish Engulfing', 'Shooting Star', 'Doji']:
        sell_signals.append(indicator_names['Candlestick'])

    # Подсчёт количества положительных сигналов
    buy_signals_count = len(buy_signals)
    sell_signals_count = len(sell_signals)

    # Порог для мажоритарного правила (минимум 3 из индикаторов)
    threshold = 3

    # Фильтрация сигналов на основе общего тренда
    if trend == 'uptrend':
        # Принимаем только ЛОНГ сигналы
        if buy_signals_count >= threshold:
            signal = (
                f"📈 **ЛОНГ сигнал для {symbol} на таймфрейме {timeframe}!** {buy_signals_count} индикаторов согласны.\n"
                f"Индикаторы: {', '.join(buy_signals)}"
            )
            logger.info(f"✅ Сгенерирован ЛОНГ сигнал для {symbol} на таймфрейме {timeframe} с индикаторами: {', '.join(buy_signals)}")
            return signal
        else:
            logger.info(f"🔕 ЛОНГ сигнал не был сгенерирован для {symbol} на таймфрейме {timeframe}.")
            return None
    elif trend == 'downtrend':
        # Принимаем только ШОРТ сигналы
        if sell_signals_count >= threshold:
            signal = (
                f"📉 **ШОРТ сигнал для {symbol} на таймфрейме {timeframe}!** {sell_signals_count} индикаторов согласны.\n"
                f"Индикаторы: {', '.join(sell_signals)}"
            )
            logger.info(f"✅ Сгенерирован ШОРТ сигнал для {symbol} на таймфрейме {timeframe} с индикаторами: {', '.join(sell_signals)}")
            return signal
        else:
            logger.info(f"🔕 ШОРТ сигнал не был сгенерирован для {symbol} на таймфрейме {timeframe}.")
            return None
    else:
        # Боковой тренд, можем пропустить или принимать оба типа сигналов
        logger.info(f"➡️ Боковой тренд для {symbol}. Сигналы не генерируются.")
        return None

# ======================= Функция для распознавания свечных паттернов =======================

def detect_candlestick_pattern(row):
    """Распознаёт простые свечные паттерны."""
    open_price = row['open']
    high_price = row['high']
    low_price = row['low']
    close_price = row['close']

    body = abs(close_price - open_price)
    upper_shadow = high_price - max(close_price, open_price)
    lower_shadow = min(close_price, open_price) - low_price

    if body < (upper_shadow + lower_shadow) * 0.1:
        return 'Doji'
    elif body > (upper_shadow + lower_shadow) * 2 and close_price > open_price:
        return 'Bullish Engulfing'
    elif body > (upper_shadow + lower_shadow) * 2 and close_price < open_price:
        return 'Bearish Engulfing'
    elif lower_shadow > body * 2 and close_price > open_price:
        return 'Hammer'
    elif upper_shadow > body * 2 and close_price < open_price:
        return 'Shooting Star'
    else:
        return 'No Pattern'

# ======================= Команды Бота =======================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start для подписки на сигналы."""
    chat_id = update.effective_chat.id
    subscribers = load_subscribers()
    if chat_id not in subscribers:
        subscribers.add(chat_id)
        save_subscribers(subscribers)
        await update.message.reply_text('✅ Вы подписались на торговые сигналы!')
        logger.info(f"👤 Пользователь {chat_id} подписался на сигналы.")
    else:
        await update.message.reply_text('ℹ️ Вы уже подписаны на торговые сигналы.')
        logger.info(f"👤 Пользователь {chat_id} попытался подписаться повторно.")

async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /stop для отписки от сигналов."""
    chat_id = update.effective_chat.id
    subscribers = load_subscribers()
    if chat_id in subscribers:
        subscribers.remove(chat_id)
        save_subscribers(subscribers)
        await update.message.reply_text('🛑 Вы отписались от торговых сигналов.')
        logger.info(f"👤 Пользователь {chat_id} отписался от сигналов.")
    else:
        await update.message.reply_text('ℹ️ Вы не были подписаны на сигналы.')
        logger.info(f"👤 Пользователь {chat_id} попытался отписаться, но не был подписан.")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /help для отображения справки."""
    help_text = (
        "ℹ️ **Доступные команды:**\n\n"
        "/start - **Подписаться** на торговые сигналы\n"
        "/stop - **Отписаться** от торговых сигналов\n"
        "/help - **Получить** справку"
    )
    await update.message.reply_text(help_text, parse_mode='Markdown')
    logger.info(f"👤 Пользователь {update.effective_chat.id} запросил справку.")

# ======================= Обработчик Ошибок =======================

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик ошибок."""
    logger.error(msg="❌ Exception while handling an update:", exc_info=context.error)

# ======================= Функция для Отправки Сигналов =======================

async def send_signal(context: ContextTypes.DEFAULT_TYPE):
    """Отправляет торговые сигналы всем подписчикам для каждой криптовалюты."""
    subscribers = load_subscribers()
    if not subscribers:
        logger.info("🔕 Нет подписчиков для отправки сигналов.")
        return  # Нет подписчиков

    tasks = []
    for symbol in CRYPTO_SYMBOLS:
        trend = get_market_trend(symbol)
        if trend is None:
            logger.warning(f"⚠️ Не удалось определить тренд для {symbol}. Пропускаем.")
            continue
        for timeframe in TIMEFRAMES:
            tasks.append(process_symbol(context, symbol, timeframe, subscribers, trend))
    await asyncio.gather(*tasks)

async def process_symbol(context: ContextTypes.DEFAULT_TYPE, symbol: str, timeframe: str, subscribers: set, trend: str):
    """Обрабатывает данные и отправляет сигнал для одной криптовалюты на определенном таймфрейме."""
    # Загрузка и обновление исторических данных
    df = load_historical_data(symbol, timeframe)
    if df.empty:
        logger.error(f"❌ Не удалось загрузить исторические данные для {symbol} на таймфрейме {timeframe}. Сигналы не будут отправлены.")
        return

    df = get_crypto_data(df, symbol, timeframe)
    if df.empty:
        logger.error(f"❌ DataFrame пуст после обновления данных для {symbol} на таймфрейме {timeframe}. Сигналы не будут отправлены.")
        return

    # Анализ данных и генерация сигнала
    signal = analyze_data(df, symbol, timeframe, trend)

    if signal:
        for chat_id in subscribers:
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"🔔 **Сигнал для {symbol} на таймфрейме {timeframe}:**\n{signal}",
                    parse_mode='Markdown'
                )
                logger.info(f"📩 Сигнал для {symbol} на таймфрейме {timeframe} отправлен пользователю {chat_id}.")
                await asyncio.sleep(0.1)  # Небольшая задержка, чтобы избежать превышения лимитов
            except Exception as e:
                logger.error(f"❌ Не удалось отправить сообщение пользователю {chat_id}: {e}")
    else:
        logger.info(f"🔕 Сигнал не был сгенерирован для {symbol} на таймфрейме {timeframe}.")

# ======================= Основная Функция =======================

def main():
    """Основная функция для запуска бота."""
    # Создание приложения Telegram
    application = ApplicationBuilder().token(API_TOKEN).build()

    # Добавление обработчиков команд
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("stop", stop))
    application.add_handler(CommandHandler("help", help_command))

    # Добавление обработчика ошибок
    application.add_error_handler(error_handler)

    # Настройка задачи, которая выполняется каждую минуту
    job_queue = application.job_queue
    job_queue.run_repeating(send_signal, interval=60, first=10)  # Период: 60 секунд, запуск через 10 секунд

    # Запуск бота
    logger.info("🚀 Бот запущен и готов к работе.")
    application.run_polling()

# ======================= Запуск Бота =======================

if __name__ == '__main__':
    main()