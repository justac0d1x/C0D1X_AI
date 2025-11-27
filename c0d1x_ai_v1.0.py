import os
import asyncio
import base64
import html
import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes


VOIDAI_API_KEY = os.getenv('VOIDAI_API_KEY')
VOIDAI_TEXT_URL = 'https://api.voidai.app/v1/chat/completions'
VOIDAI_IMAGE_URL = 'https://api.voidai.app/v1/images/generations'

text_queue = asyncio.Queue()
image_queue = asyncio.Queue()

user_models = {}

AVAILABLE_MODELS = {
    'gpt-3.5-turbo': '⚡ GPT-3.5 Turbo (быстрая)',
    'gpt-4o-mini': '🚀 GPT-4o Mini (рекомендуется)',
    'gpt-4o': '💎 GPT-4o (мощная)',
    'chatgpt-4o-latest': '🔥 ChatGPT-4o Latest',
    'o3-mini': '🧠 O3 Mini',
    'o4-mini': '🌟 O4 Mini',
    'gpt-5-mini': '📝 GPT-5 Mini',
    'gpt-5': '💻 GPT-5',
    'gpt-4o-mini-search-preview': '🔮 GPT-4o Search',
    'gemini-2.0-flash': '⚡ Gemini 2.0 Flash',
    'gemini-2.5-flash': '💫 Gemini 2.5 Flash',
    'lumina': '⚙️ Lumina AI',
    'grok-4': '🦾 Grok 4',
    'deepseek-r1': '🔍 DeepSeek R1',
    'deepseek-v3': '💡 DeepSeek V3'
}


def escape_html(text):
    """Экранирует HTML-символы в тексте"""
    if text is None:
        return None
    return html.escape(text)


def extract_thoughts(text):
    """Извлекает размышления (thoughts) из текста"""
    import re
    
    thoughts = None
    content = text
    
    # Ищем блок размышлений в начале (для deepseek и подобных)
    thought_match = re.search(r'<think>(.*?)</think>', text, re.DOTALL)
    if thought_match:
        thoughts = thought_match.group(1).strip()
        content = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()
    
    return thoughts, content


def split_text_by_length(text, max_length=4000):
    """Разбивает текст на части по max_length символов"""
    if len(text) <= max_length:
        return [text]
    
    parts = []
    start = 0
    text_length = len(text)
    
    while start < text_length:
        # Определяем конец текущего фрагмента
        end = start + max_length
        
        # Если дошли до конца текста
        if end >= text_length:
            parts.append(text[start:])
            break
        
        # Ищем ближайший перенос строки или пробел для красивого разбиения
        break_pos = text.rfind('\n', start, end)
        if break_pos == -1:
            break_pos = text.rfind(' ', start, end)
        if break_pos == -1:
            break_pos = text.rfind('.', start, end)
        if break_pos == -1:
            break_pos = text.rfind(',', start, end)
        if break_pos == -1:
            # Если не нашли подходящего места для разрыва, разбиваем по max_length
            break_pos = end
        
        # Добавляем фрагмент
        parts.append(text[start:break_pos].strip())
        
        # Переходим к следующему фрагменту
        start = break_pos
        
        # Пропускаем пробелы и переносы строк
        while start < text_length and text[start] in [' ', '\n', '\r', '\t']:
            start += 1
    
    return parts if parts else [text]


def format_with_thoughts_chunked(thoughts, content):
    """Форматирует сообщение с размышлениями и ответом, разбивая на куски"""
    messages = []
    
    # Обработаем размышления
    if thoughts:
        escaped_thoughts = escape_html(thoughts)
        thought_parts = split_text_by_length(escaped_thoughts, 3500)
        for i, part in enumerate(thought_parts):
            if i == 0:
                messages.append(f"✅ Размышления:\n\n<i>{part}</i>")
            else:
                messages.append(f"✅ Размышления (продолжение):\n\n<i>{part}</i>")
    
    # Обработаем ответ
    escaped_content = escape_html(content)
    content_parts = split_text_by_length(escaped_content, 3500)
    for i, part in enumerate(content_parts):
        if i == 0 and not thoughts:  # Если нет размышлений, это первый блок ответа
            messages.append(f"✅ Результат:\n\n<code>{part}</code>")
        elif i == 0:
            messages.append(f"✅ Ответ:\n\n<code>{part}</code>")
        else:
            messages.append(f"✅ Ответ (продолжение):\n\n<code>{part}</code>")
    
    return messages


async def send_formatted_messages(context, chat_id, messages):
    """Отправляет список сообщений"""
    for message in messages:
        try:
            await context.bot.send_message(chat_id=chat_id, text=message, parse_mode='HTML')
        except Exception as e:
            # Если возникает ошибка парсинга HTML, отправляем как обычный текст
            print(f"Ошибка отправки HTML: {e}")
            plain_text = message.replace('<i>', '').replace('</i>', '').replace('<code>', '').replace('</code>', '')
            plain_text_parts = split_text_by_length(plain_text, 4000)
            for part in plain_text_parts:
                await context.bot.send_message(chat_id=chat_id, text=part)


async def process_text_queue():
    """Обработчик очереди для генерации текста"""
    while True:
        try:
            chat_id, prompt, model, context = await text_queue.get()
            
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"🔄 Генерирую текст с помощью \n{AVAILABLE_MODELS.get(model, model)}..."
            )
            
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.post(
                    VOIDAI_TEXT_URL,
                    headers={
                        'Authorization': f'Bearer {VOIDAI_API_KEY}',
                        'Content-Type': 'application/json'
                    },
                    json={
                        'model': model,
                        'messages': [
                            {'role': 'user', 'content': prompt}
                        ]
                    }
                )
                
                if response.status_code == 200:
                    data = response.json()
                    text = data['choices'][0]['message']['content']
                    thoughts, content = extract_thoughts(text)
                    
                    # Проверяем длину всего ответа
                    total_length = (len(thoughts) if thoughts else 0) + len(content)
                    if total_length > 3500:
                        # Если ответ длинный, разбиваем на части
                        messages = format_with_thoughts_chunked(thoughts, content)
                        await send_formatted_messages(context, chat_id, messages)
                    else:
                        # Если ответ короткий, отправляем как есть
                        if thoughts:
                            final_message = f"✅ Размышления:\n\n<i>{escape_html(thoughts)}</i>\n\n✅ Ответ:\n\n<code>{escape_html(content)}</code>"
                        else:
                            final_message = f"✅ Результат:\n\n<code>{escape_html(content)}</code>"
                        await context.bot.send_message(chat_id=chat_id, text=final_message, parse_mode='HTML')
                        
                else:
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=f"❌ Ошибка: {response.status_code} - {response.text}"
                    )
            
            text_queue.task_done()
            
        except Exception as e:
            print(f"Ошибка в обработчике текста: {e}")
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"❌ Ошибка: {str(e)}"
                )
            except:
                pass
            text_queue.task_done()


async def process_image_queue():
    """Обработчик очереди для генерации изображений"""
    while True:
        try:
            chat_id, prompt, context = await image_queue.get()
            
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"🎨 Генерирую изображение..."
            )
            
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.post(
                    VOIDAI_IMAGE_URL,
                    headers={
                        'Authorization': f'Bearer {VOIDAI_API_KEY}',
                        'Content-Type': 'application/json'
                    },
                    json={
                        'model': 'gpt-image-1',
                        'prompt': prompt,
                        'size': '1024x1024',
                        'quality': 'standard',
                        'n': 1
                    }
                )
                
                if response.status_code == 200:
                    data = response.json()
                    if data.get('data') and data['data'][0].get('b64_json'):
                        b64_image = data['data'][0]['b64_json']
                        image_bytes = base64.b64decode(b64_image)
                        await context.bot.send_photo(
                            chat_id=chat_id,
                            photo=image_bytes,
                            caption=f"✅ Изображение по запросу: <b>{escape_html(prompt)}</b>",
                            parse_mode='HTML'
                        )
                    else:
                        await context.bot.send_message(
                            chat_id=chat_id,
                            text="❌ Не удалось получить изображение из ответа API"
                        )
                else:
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=f"❌ Ошибка: {response.status_code} - {response.text}"
                    )
            
            image_queue.task_done()
            
        except Exception as e:
            print(f"Ошибка в обработчике изображений: {e}")
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"❌ Ошибка: {str(e)}"
                )
            except:
                pass
            image_queue.task_done()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_message = """Привет! 👋🏼 
Я -  телеграм бот <b>C0D1X AI</b>, созданный для генерации текста и картинок с помощью современных ИИ. Давай начнем это делать прямо сейчас!

Все команды находятся во вкладке <b>"Меню"</b>, и я очень надеюсь, что ты прочитал <b>правила использования бота</b>! Стараюсь работать 24/7!⚡"""
    
    keyboard = [
        [InlineKeyboardButton("📋 Правила", callback_data="show_rules"), InlineKeyboardButton("👤 Автор", callback_data="show_author")],
        [InlineKeyboardButton("✅ Понятно", callback_data="close_start")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    parts = split_text_by_length(welcome_message, 4000)
    for part in parts:
        await update.message.reply_text(part, parse_mode='HTML', reply_markup=reply_markup if part == parts[-1] else None)


async def rules(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rules_message = """📋 <b>Правила использования бота:</b>
Т.к. запросы бота обрабатывает сервер, использущий API поставщиков нейросетей <i>(Void AI)</i>,
все запросы пользователей  - общие для тарифов <b>Void AI</b>, по этому при нарушении общих правил
пользователями окажется под угрозой вся инфраструктура <b>C0D1X AI</b>. Тем не менее, мы уважаем
анонимность пользователей и не храним <b>НИКАКИХ</b> данных, звязанных с пользователями,
тем более истории сообщений, их данные и т.д. 
Надеемся на совесть пользователей и выполнение ими правил.
<i>Генерируя люой контент, вы автоматически соглашаетесь с правилами использования бота.</i>

Что ЗАПРЕЩЕНО делать:
- Генерировать любой контент, связанный с нецензурной лексикой, призывами к насилию, пропагандой
ультраправых организаций и т.д.
- Генерировать любой контент, связанный с предпренимательской деятельностью и рекламой."""
    
    keyboard = [[InlineKeyboardButton("✅ Понятно", callback_data="close_rules")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    parts = split_text_by_length(rules_message, 4000)
    for part in parts:
        await update.message.reply_text(part, parse_mode='HTML', reply_markup=reply_markup if part == parts[-1] else None)


async def queue_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /queue - статус очередей"""
    text_size = text_queue.qsize()
    image_size = image_queue.qsize()
    
    status_message = f"""
📊 Статус очередей:

📝 Текстовые запросы: {text_size}
🎨 Генерация изображений: {image_size}
    """
    await update.message.reply_text(status_message)


async def select_model(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /model - выбор модели для генерации текста"""
    user_id = update.effective_user.id
    current_model = user_models.get(user_id, 'gpt-4o-mini')
    
    keyboard = []
    for model_id, model_name in AVAILABLE_MODELS.items():
        marker = "✓ " if model_id == current_model else ""
        button_text = f"{marker}{model_name}"
        keyboard.append([InlineKeyboardButton(button_text, callback_data=f"model:{model_id}")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"🤖 Выберите модель для генерации текста:\n\n"
        f"Текущая модель: {AVAILABLE_MODELS.get(current_model, current_model)}",
        reply_markup=reply_markup
    )


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик нажатий на inline кнопки"""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    data = query.data
    
    if data == "close_rules" or data == "close_start":
        await query.delete_message()
    elif data == "show_rules":
        await query.delete_message()
        rules_text = """📋 <b>Правила использования бота:</b>
Т.к. запросы бота обрабатывает сервер, использущий API поставщиков нейросетей <i>(Void AI)</i>,
все запросы пользователей  - общие для тарифов <b>Void AI</b>, по этому при нарушении общих правил
пользователями окажется под угрозой вся инфраструктура <b>C0D1X AI</b>. Тем не менее, мы уважаем
анонимность пользователей и не храним <b>НИКАКИХ</b> данных, звязанных с пользователями,
тем более истории сообщений, их данные и т.д. 
Надеемся на совесть пользователей и выполнение ими правил.
<i>Генерируя люой контент, вы автоматически соглашаетесь с правилами использования бота.</i>

Что ЗАПРЕЩЕНО делать:
- Генерировать любой контент, связанный с нецензурной лексикой, призывами к насилию, пропагандой
ультраправых организаций и т.д.
- Генерировать любой контент, связанный с предпренимательской деятельностью и рекламой."""
        parts = split_text_by_length(rules_text, 4000)
        for part in parts:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=part,
                parse_mode='HTML',
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ Понятно", callback_data="close_rules")]]) if part == parts[-1] else None
            )
    elif data == "show_author":
        await query.answer("Переводим к автору...", show_alert=False)
        keyboard = [[InlineKeyboardButton("👤 Перейти к автору", url="https://t.me/C0DIX_X")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            text="👤 <b>Автор бота:</b>\n\nНажми на кнопку ниже, чтобы перейти к автору!",
            parse_mode='HTML',
            reply_markup=reply_markup
        )
    elif data.startswith("model:"):
        model_id = data[6:]
        
        if model_id in AVAILABLE_MODELS:
            user_models[user_id] = model_id
            await query.edit_message_text(
                f"✅ Модель изменена на: {AVAILABLE_MODELS[model_id]}\n\n"
                f"Теперь все ваши запросы /text будут использовать эту модель."
            )
        else:
            await query.edit_message_text("❌ Неизвестная модель")


async def generate_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /text - генерация текста"""
    if not context.args:
        await update.message.reply_text(
            "❌ Пожалуйста, укажите запрос.\nПример: /text Расскажи анекдот"
        )
        return
    
    prompt = ' '.join(context.args)
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    
    model = user_models.get(user_id, 'gpt-4o-mini')
    
    await text_queue.put((chat_id, prompt, model, context))


async def generate_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /image - генерация изображения"""
    if not context.args:
        await update.message.reply_text(
            "❌ Пожалуйста, укажите описание изображения.\nПример: /image cute cat playing"
        )
        return
    
    prompt = ' '.join(context.args)
    chat_id = update.effective_chat.id
    
    await image_queue.put((chat_id, prompt, context))


async def handle_invalid_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик неправильных команд или обычных сообщений"""
    message_text = update.message.text
    
    # Проверяем, является ли это попыткой команды
    if message_text.startswith('/'):
        # Это команда, но она не распознана
        error_text = (f"❌ <b>Неизвестная команда:</b> <code>{escape_html(message_text)}</code>\n\n"
                     f"Доступные команды:\n"
                     f"• <code>/start</code> - справка и приветствие\n"
                     f"• <code>/text [запрос]</code> - генерация текста\n"
                     f"• <code>/image [описание]</code> - генерация изображения\n"
                     f"• <code>/model</code> - выбор модели\n"
                     f"• <code>/queue</code> - статус очередей\n"
                     f"• <code>/rules</code> - правила использования\n\n"
                     f"Используйте /rules для подробного описания каждой команды.")
        parts = split_text_by_length(error_text, 4000)
        for part in parts:
            await update.message.reply_text(part, parse_mode='HTML')
    else:
        # Просто обычное сообщение без команды
        await update.message.reply_text(
            "ℹ️ Это не команда! Все команды должны начинаться с <code>/</code>\n\n"
            "Используйте:\n"
            "• <code>/text [запрос]</code> - для генерации текста\n"
            "• <code>/image [описание]</code> - для генерации изображения\n"
            "• <code>/start</code> - для справки",
            parse_mode='HTML'
        )


async def post_init(application: Application):
    """Запуск фоновых обработчиков очередей"""
    asyncio.create_task(process_text_queue())
    asyncio.create_task(process_image_queue())
    print("🚀 Обработчики очередей запущены!")


def main():
    """Запуск бота"""
    telegram_token = os.getenv('TELEGRAM_BOT_TOKEN')
    
    if not telegram_token:
        print('❌ Ошибка: TELEGRAM_BOT_TOKEN не установлен!')
        return
    
    if not VOIDAI_API_KEY:
        print('❌ Ошибка: VOIDAI_API_KEY не установлен!')
        return
    
    application = Application.builder().token(telegram_token).post_init(post_init).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("rules", rules))
    application.add_handler(CommandHandler("queue", queue_status))
    application.add_handler(CommandHandler("model", select_model))
    application.add_handler(CommandHandler("text", generate_text))
    application.add_handler(CommandHandler("image", generate_image))
    application.add_handler(CallbackQueryHandler(button_callback))
    application.add_handler(MessageHandler(filters.TEXT, handle_invalid_command))
    
    print('✅ AI Бот запущен и готов к работе 24/7!')
    print(f'📝 Текстовая очередь готова')
    print(f'🎨 Очередь изображений готова')
    
    application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == '__main__':
    main()