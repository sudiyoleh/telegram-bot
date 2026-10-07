from datetime import datetime, timezone, timedelta
import html
import math
import logging
import requests
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder

import os

# Бот безпечно завантажить токен із налаштувань сервера
TOKEN = "os.getenv("BOT_TOKEN")"

API_URL = "https://api.dimap.live/api/polling"

# Координати центрів областей
REGIONAL_CENTERS = {
    "Тернопільська обл.": (49.5535, 25.5948),
    "Хмельницька обл.": (49.4229, 26.9871),
    "Львівська обл.": (49.8397, 24.0297),
    "Івано-Франковська обл.": (48.9226, 24.7111),
    "Рівненська обл.": (50.6199, 26.2516),
    "Волинська обл.": (50.7472, 25.3254),
    "Чернівецька обл.": (48.2921, 25.9358),
    "Житомирська обл.": (50.2546, 28.6567),
    "Вінницька обл.": (49.2331, 28.4682),
    "Київська обл.": (50.4501, 30.5234),
    "Черкаська обл.": (49.4444, 32.0598),
    "Кіровоградська обл.": (48.5038, 32.2606),
    "Одеська обл.": (46.4825, 30.7233),
    "Миколаївська обл.": (46.9750, 31.9946),
    "Херсонська обл.": (46.6354, 32.6169),
    "Запорізька обл.": (47.8388, 35.1396),
    "Дніпропетровська обл.": (48.4647, 35.0462),
    "Полтавська обл.": (49.5883, 34.5514),
    "Сумська обл.": (50.9077, 34.7981),
    "Харківська обл.": (49.9935, 36.2304),
    "Чернігівська обл.": (51.4982, 31.2893),
    "Донецька обл.": (48.0159, 37.8028),
    "Луганська обл.": (48.5740, 39.3078),
    "АР Крим": (44.9572, 34.1108),
}

# Доступні міста для пошуку
CITIES_COORDS = {
    "тернопіль": (49.5535, 25.5948),
    "київ": (50.4501, 30.5234),
    "львів": (49.8397, 24.0297),
    "івано-франківськ": (48.9226, 24.7111),
    "хмельницький": (49.4229, 26.9871),
    "рівне": (50.6199, 26.2516),
    "луцьк": (50.7472, 25.3254),
    "чернівці": (48.2921, 25.9358),
    "житомир": (50.2546, 28.6567),
    "вінниця": (49.2331, 28.4682),
    "одеса": (46.4825, 30.7233),
    "харків": (49.9935, 36.2304),
    "дніпро": (48.4647, 35.0462),
    "запоріжжя": (47.8388, 35.1396),
    "ужгород": (48.6208, 22.2879),
    "черкаси": (49.4444, 32.0598),
    "кропивницький": (48.5038, 32.2606),
    "полтава": (49.5883, 34.5514),
    "суми": (50.9077, 34.7981),
    "чернігів": (51.4982, 31.2893),
    "миколаїв": (46.9750, 31.9946),
    "херсон": (46.6354, 32.6169),
}


class UserState(StatesGroup):
    waiting_for_city = State()
    active_session = State()


def calculate_distance(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )
    c = 2 * math.asin(math.sqrt(a))
    return R * c


def get_region_by_coords(lat, lng):
    min_dist = float("inf")
    closest_region = "Невідома обл."
    for region_name, (r_lat, r_lng) in REGIONAL_CENTERS.items():
        dist = calculate_distance(lat, lng, r_lat, r_lng)
        if dist < min_dist:
            min_dist = dist
            closest_region = region_name
    return closest_region


def format_time(hours_float):
    if math.isinf(hours_float) or math.isnan(hours_float) or hours_float <= 0:
        v = 0
    else:
        v = hours_float
    total_minutes = int(round(v * 60))
    hours = total_minutes // 60
    minutes = total_minutes % 60
    return f"{hours} год {minutes} хв" if hours > 0 else f"{minutes} хв"


def get_main_keyboard():
    builder = InlineKeyboardBuilder()
    builder.row(
        types.InlineKeyboardButton(text="🔄 Оновити дані", callback_data="refresh"),
        types.InlineKeyboardButton(text="🏙 Змінити місто", callback_data="change_city"),
    )
    builder.row(
        types.InlineKeyboardButton(text="❌ Вийти", callback_data="exit")
    )
    return builder.as_markup()


async def fetch_data_report(city_name, target_lat, target_lng):
    try:
        response = requests.get(API_URL, timeout=10)
        if response.status_code != 200:
            return "Помилка сервера при отриманні даних."
        data = response.json()
    except Exception as e:
        return f"Помилка підключення: {e}"

    safe_city_name = html.escape(city_name.upper())

    # 1. Таргети
    targets_list = data.get("data", {}).get("targets", {}).get("targets", [])
    processed_targets = []
    for t in targets_list:
        lat = t.get("lat")
        lng = t.get("lng")
        speed = t.get("speed", 0)
        if lat is None or lng is None:
            continue
        distance = calculate_distance(target_lat, target_lng, lat, lng)
        time_to_target_hours = distance / speed if speed > 0 else float("inf")
        processed_targets.append({
            "type": html.escape(str(t.get("type", "UNKNOWN"))),
            "distance": distance,
            "speed": speed,
            "time_hours": time_to_target_hours,
            "lat": lat,
            "lng": lng,
        })

    processed_targets.sort(key=lambda x: x["distance"])
    top_5 = processed_targets[:5]

    report = f"<b>🎯 ТОП-5 НАЙБЛИЖЧИХ ЦІЛЕЙ ДО МІСТА {safe_city_name}:</b>\n"
    report += "—" * 25 + "\n"
    if not top_5:
        report += "Наразі активних цілей немає.\n"
    else:
        for i, item in enumerate(top_5, 1):
            time_str = (
                format_time(item["time_hours"])
                if item["speed"] > 0
                else "Н/Д (швидкість 0)"
            )
            region_str = html.escape(get_region_by_coords(item["lat"], item["lng"]))
            report += (
                f"{i}. {item['type']} — <b>{item['distance']:.1f} км</b> — {time_str}"
                f" <i>({region_str})</i>\n"
            )

    # 2. Новини
    region_keyword = city_name.lower()
    news_list = data.get("data", {}).get("news", [])
    filtered_news = []
    for item in news_list:
        regions = [r.lower() for r in item.get("regions", [])]
        text = item.get("text", "").lower()
        is_matching_region = any(region_keyword in reg for reg in regions)
        is_matching_text = region_keyword in text
        if is_matching_region or is_matching_text:
            filtered_news.append(item)

    report += f"\n<b>📰 НОВИНИ / ПОВІДОМЛЕННЯ ДЛЯ {safe_city_name}:</b>\n"
    report += "—" * 25 + "\n"
    if not filtered_news:
        report += "У поточній стрічці прямих згадок немає.\n"
    else:
        for item in filtered_news:
            raw_text = item.get("text", "").replace("<br>", "\n").replace("&!", "!")
            safe_text = html.escape(raw_text)
            report += f"• {safe_text}\n"

    # Визначаємо київський час
    try:
        from zoneinfo import ZoneInfo
        kiev_time = datetime.now(ZoneInfo("Europe/Kiev"))
    except Exception:
        kiev_time = datetime.now(timezone(timedelta(hours=3)))

    report += f"\n🕒 <i>Оновлено: {kiev_time.strftime('%H:%M:%S')}</i>"
    return report


# --- ХЕНДЛЕРИ БОТА ---


async def cmd_start(message: types.Message, state: FSMContext):
    await state.set_state(UserState.waiting_for_city)
    await message.answer(
        "Вітаю! Введіть назву населеного пункту (наприклад: <b>Тернопіль</b>,"
        " <b>Київ</b>, <b>Львів</b> тощо):",
        parse_mode="HTML",
    )


async def process_city_input(message: types.Message, state: FSMContext):
    city_input = message.text.strip().lower()
    if city_input in CITIES_COORDS:
        lat, lng = CITIES_COORDS[city_input]
        city_name = city_input.capitalize()

        await state.update_data(
            city_name=city_name, target_lat=lat, target_lng=lng
        )
        await state.set_state(UserState.active_session)

        report = await fetch_data_report(city_name, lat, lng)
        await message.answer(
            report, parse_mode="HTML", reply_markup=get_main_keyboard()
        )
    else:
        await message.answer(
            "Не знайдено такого міста у базі. Спробуйте ще раз ввести назву (наприклад,"
            " Тернопіль):"
        )


async def callback_handler(callback: types.CallbackQuery, state: FSMContext):
    action = callback.data
    data = await state.get_data()
    city_name = data.get("city_name")
    lat = data.get("target_lat")
    lng = data.get("target_lng")

    if action == "refresh":
        if not city_name:
            await callback.message.answer("Будь ласка, почніть спочатку з команди /start")
            return
        report = await fetch_data_report(city_name, lat, lng)
        try:
            await callback.message.edit_text(
                report, parse_mode="HTML", reply_markup=get_main_keyboard()
            )
        except Exception:
            pass
        await callback.answer("Дані оновлено!")

    elif action == "change_city":
        await state.set_state(UserState.waiting_for_city)
        await callback.message.answer("Введіть нову назву населеного пункту:")
        await callback.answer()

    elif action == "exit":
        await state.clear()
        await callback.message.answer(
            "Роботу зупинено. Щоб почати знову, введіть /start"
        )
        await callback.answer()


async def main():
    logging.basicConfig(level=logging.INFO)
    bot = Bot(token=TOKEN)
    dp = Dispatcher(storage=MemoryStorage())

    dp.message.register(cmd_start, Command("start"))
    dp.message.register(process_city_input, UserState.waiting_for_city)
    dp.callback_query.register(
        callback_handler, UserState.active_session, F.data.in_(["refresh", "change_city", "exit"])
    )
    dp.message.register(process_city_input, UserState.active_session)

    print("Бот запущено успішно...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
