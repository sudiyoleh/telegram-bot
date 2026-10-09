from datetime import datetime, timezone, timedelta
import html
import math
import logging
import requests
import reverse_geocode
import re
import asyncio
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder
import os

# Бот безпечно завантажить токен із налаштувань сервера
TOKEN = os.getenv("BOT_TOKEN")

API_URL = "https://api.dimap.live/api/polling"

# Зберігаємо активні задачі автооновлення для кожного чату: {chat_id: asyncio.Task}
active_tasks = {}

# Доступні міста для вибору користувача
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

REGIONS_UA = {
    "Poltava": "Полтавська обл.",
    "Kherson": "Херсонська обл.",
    "Lviv": "Львівська обл.",
    "Ternopil": "Тернопільська обл.",
    "Kyiv": "Київська обл.",
    "Khmelnytskyi": "Хмельницька обл.",
    "Ivano-Frankivsk": "Івано-Франківська обл.",
    "Rivne": "Рівненська обл.",
    "Volyn": "Волинська обл.",
    "Chernivtsi": "Чернівецька обл.",
    "Zhytomyr": "Житомирська обл.",
    "Vinnytsia": "Вінницька обл.",
    "Cherkasy": "Черкаська обл.",
    "Kirovohrad": "Кіровоградська обл.",
    "Odesa": "Одеська обл.",
    "Mykolaiv": "Миколаївська обл.",
    "Zaporizhzhia": "Запорізька обл.",
    "Dnipropetrovsk": "Дніпропетровська обл.",
    "Poltava": "Полтавська обл.",
    "Sumy": "Сумська обл.",
    "Kharkiv": "Харківська обл.",
    "Chernihiv": "Чернігівська обл.",
    "Donetsk": "Донецька обл.",
    "Luhansk": "Луганська обл.",
    "Crimea": "АР Крим"
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


def get_location_details(lat, lng):
    """Визначає населений пункт та регіон і перекладає українською"""
    try:
        res = reverse_geocode.get((lat, lng))
        city = res.get('city', '')
        state = res.get('state', '')

        state_ua = REGIONS_UA.get(state, state)

        if city and state_ua:
            return f"{city}, {state_ua}"
        return city or state_ua or "Україна"
    except Exception:
        return "Невідома локація"


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

    targets_data = data.get("data", {}).get("targets", {})
    total_active = targets_data.get("totalActiveTargets", len(targets_data.get("targets", [])))

    user_location_res = reverse_geocode.get((target_lat, target_lng))
    user_state_eng = user_location_res.get('state', '')
    user_region_ua = REGIONS_UA.get(user_state_eng, user_state_eng)

    targets_list = targets_data.get("targets", [])
    processed_targets = []
    region_targets_count = 0

    for t in targets_list:
        lat = t.get("lat")
        lng = t.get("lng")
        if lat is None or lng is None:
            continue

        t_type = str(t.get("type", "UNKNOWN")).strip().upper()

        speed = t.get("speed", 0)
        if not speed or speed <= 0:
            if "REACTIVE_SHAHED" in t_type:
                speed = 500
            elif "SHAHED" in t_type:
                speed = 200
            elif "FPV" in t_type:
                speed = 150
            elif "SCOUT" in t_type:
                speed = 90
            else:
                speed = 300

        distance = calculate_distance(target_lat, target_lng, lat, lng)
        time_to_target_hours = distance / speed if speed > 0 else float("inf")

        loc_name = get_location_details(lat, lng)

        if user_region_ua and user_region_ua in loc_name:
            region_targets_count += 1

        processed_targets.append({
            "type": html.escape(t_type),
            "distance": distance,
            "speed": speed,
            "time_hours": time_to_target_hours,
            "location_name": html.escape(loc_name),
        })

    processed_targets.sort(key=lambda x: x["distance"])
    top_5 = processed_targets[:5]

    report = f"Наразі в Україні {total_active} активних об'єктів."
    if user_region_ua:
        report += f" З них у {user_region_ua}: <b>{region_targets_count}</b>.\n\n"
    else:
        report += "\n\n"

    report += f"<b>🎯 ТОП-5 НАЙБЛИЖЧИХ ЦІЛЕЙ ДО МІСТА {safe_city_name}:</b>\n"
    report += "—" * 25 + "\n"
    if not top_5:
        report += "Наразі активних цілей немає.\n"
    else:
        for i, item in enumerate(top_5, 1):
            time_str = format_time(item["time_hours"])
            loc_str = item["location_name"]
            report += (
                f"{i}. {item['type']} — <b>{item['distance']:.1f} км</b> — {time_str}"
                f" <i>({loc_str})</i>\n"
            )

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
            raw_text = item.get("text", "")
            clean_text = re.sub(r'<[^>]+>', '', raw_text)
            clean_text = clean_text.replace("&!", "!")
            safe_text = html.escape(clean_text)
            report += f"• {safe_text}\n"

    try:
        from zoneinfo import ZoneInfo
        kiev_time = datetime.now(ZoneInfo("Europe/Kiev"))
    except Exception:
        kiev_time = datetime.now(timezone(timedelta(hours=3)))

    report += f"\n🕒 <i>Оновлено: {kiev_time.strftime('%H:%M:%S')}</i>"
    return report


# --- ФОНОВЕ АВТООНОВЛЕННЯ ---

async def start_auto_updater(bot: Bot, chat_id: int, state: FSMContext):
    """Фонова задача, яка щохвилини оновлює звіт у чаті"""
    # Скасовуємо попередню задачу для цього чату, якщо вона була
    if chat_id in active_tasks:
        active_tasks[chat_id].cancel()

    async def updater_loop():
        try:
            while True:
                await asyncio.sleep(60)
                data = await state.get_data()
                city_name = data.get("city_name")
                lat = data.get("target_lat")
                lng = data.get("target_lng")
                msg_id = data.get("message_id")

                if not city_name or not lat or not lng or not msg_id:
                    break

                # Отримуємо свіжий звіт
                report = await fetch_data_report(city_name, lat, lng)

                # Перевіряємо, чи є тривога/об'єкти у відповідній області
                user_location_res = reverse_geocode.get((lat, lng))
                user_state_eng = user_location_res.get('state', '')
                user_region_ua = REGIONS_UA.get(user_state_eng, user_state_eng)

                # Перевіримо, чи є цілі в регіоні (можна також перевірити через повторний підрахунок або перенесення логіки)
                try:
                    await bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=msg_id,
                        text=report,
                        parse_mode="HTML",
                        reply_markup=get_main_keyboard()
                    )
                except Exception:
                    # Якщо повідомлення не змінилося або видалене, просто пропускаємо
                    pass
        except asyncio.CancelledError:
            pass
        finally:
            if chat_id in active_tasks:
                del active_tasks[chat_id]

    task = asyncio.create_task(updater_loop())
    active_tasks[chat_id] = task


# --- ХЕНДЛЕРИ БОТА ---

async def cmd_start(message: types.Message, state: FSMContext):
    chat_id = message.chat.id
    if chat_id in active_tasks:
        active_tasks[chat_id].cancel()
        del active_tasks[chat_id]

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

        report = await fetch_data_report(city_name, lat, lng)
        sent_message = await message.answer(
            report, parse_mode="HTML", reply_markup=get_main_keyboard()
        )

        await state.update_data(
            city_name=city_name,
            target_lat=lat,
            target_lng=lng,
            message_id=sent_message.message_id
        )
        await state.set_state(UserState.active_session)

        # Запускаємо фонове автооновлення для цього чату
        await start_auto_updater(message.bot, message.chat.id, state)
    else:
        await message.answer(
            "Не знайдено такого міста у базі. Спробуйте ще раз ввести назву (наприклад,"
            " Тернопіль):"
        )


async def callback_handler(callback: types.CallbackQuery, state: FSMContext):
    action = callback.data
    chat_id = callback.message.chat.id
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
        if chat_id in active_tasks:
            active_tasks[chat_id].cancel()
            del active_tasks[chat_id]

        await state.set_state(UserState.waiting_for_city)
        await callback.message.answer("Введіть нову назву населеного пункту:")
        await callback.answer()

    elif action == "exit":
        if chat_id in active_tasks:
            active_tasks[chat_id].cancel()
            del active_tasks[chat_id]

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
    try:
        await dp.start_polling(bot)
    finally:
        for task in active_tasks.values():
            task.cancel()


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
