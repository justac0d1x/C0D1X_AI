import os
import asyncio
import base64
import html
import httpx
import threading
import time
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

# Конфигурация
CONFIG = {
    'VOIDAI_API_KEY': os.getenv('VOIDAI_API_KEY'),
    'TELEGRAM_TOKEN': os.getenv('TELEGRAM_BOT_TOKEN'),
    'VOIDAI_TEXT_URL': 'https://api.voidai.app/v1/chat/completions',
    'VOIDAI_IMAGE_URL': 'https://api.voidai.app/v1/images/generations',
    'MAX_MESSAGE_LENGTH': 4000,
    'MAX_HTML_LENGTH': 3500,
    'REQUEST_TIMEOUT': 120.0,
    'PORT': int(os.getenv('PORT', 8080)),
    'SELF_PING_INTERVAL': 300,  # Пинг каждые 5 минут
    'HEALTH_CHECK_PORT': int(os.getenv('PORT', 8080))
}

# Модели AI
MODELS = {
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

# Глобальные переменные
text_queue = asyncio.Queue()
image_queue = asyncio.Queue()
user_models = {}
bot_start_time = datetime.now()

class KeepAliveServer:
    """Простой HTTP сервер для поддержания активности на Render.com"""
    
    def __init__(self, port=8080):
        self.port = port
        self.server = None
        self.thread = None
    
    def start(self):
        """Запускает HTTP сервер в отдельном потоке"""
        def run_server():
            from http.server import HTTPServer, BaseHTTPRequestHandler
            import json
            
            class HealthHandler(BaseHTTPRequestHandler):
                def do_GET(self):
                    if self.path == '/health':
                        self.send_response(200)
                        self.send_header('Content-type', 'application/json')
                        self.end_headers()
                        uptime = datetime.now() - bot_start_time
                        response = {
                            'status': 'healthy',
                            'bot_uptime': str(uptime),
                            'text_queue_size': text_queue.qsize(),
                            'image_queue_size': image_queue.qsize(),
                            'timestamp': datetime.now().isoformat()
                        }
                        self.wfile.write(json.dumps(response, indent=2, ensure_ascii=False).encode())
                    elif self.path == '/':
                        self.send_response(200)
                        self.send_header('Content-type', 'text/html')
                        self.end_headers()
                        self.wfile.write(b'<html><body><h1>C0D1X AI Bot is running!</h1><p><a href="/health">Health Check</a></p></body></html>')
                    else:
                        self.send_response(404)
                        self.end_headers()
                
                def log_message(self, format, *args):
                    # Отключаем стандартное логирование
                    pass
            
            self.server = HTTPServer(('0.0.0.0', self.port), HealthHandler)
            print(f"🔄 Keep-alive сервер запущен на порту {self.port}")
            self.server.serve_forever()
        
        self.thread = threading.Thread(target=run_server, daemon=True)
        self.thread.start()
        return self
    
    def stop(self):
        """Останавливает HTTP сервер"""
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            print("🔴 Keep-alive сервер остановлен")

class SelfPinger:
    """Класс для самопинга чтобы избежать сна"""
    
    def __init__(self, interval=300):
        self.interval = interval
        self.is_running = False
        self.task = None
    
    async def start(self):
        """Запускает периодический самопинг"""
        self.is_running = True
        print(f"🔄 Самопинг запущен с интервалом {self.interval} секунд")
        
        while self.is_running:
            try:
                # Пингуем сами себя
                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.get(f'http://localhost:{CONFIG["PORT"]}/health')
                    if response.status_code == 200:
                        print(f"✅ Самопинг успешен: {datetime.now().strftime('%H:%M:%S')}")
                    else:
                        print(f"⚠️ Самопинг неудачен: статус {response.status_code}")
            except Exception as e:
                print(f"❌ Ошибка самопинга: {e}")
            
            # Ждем указанный интервал
            for _ in range(self.interval):
                if not self.is_running:
                    break
                await asyncio.sleep(1)
    
    def stop(self):
        """Останавливает самопинг"""
        self.is_running = False
        print("🔴 Самопинг остановлен")

class MessageProcessor:
    """Класс для обработки и форматирования сообщений"""
    
    @staticmethod
    def escape_html(text):
        """Экранирует HTML-символы"""
        return html.escape(text) if text else None

    @staticmethod
    def extract_thoughts(text):
        """Извлекает мысли из текста"""
        import re
        thought_match = re.search(r'<think>(.*?)</think>', text, re.DOTALL)
        if thought_match:
            thoughts = thought_match.group(1).strip()
            content = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()
            return thoughts, content
        return None, text

    @staticmethod
    def split_text(text, max_length=4000):
        """Разбивает текст на части оптимальным образом"""
        if len(text) <= max_length:
            return [text]
        
        parts = []
        start = 0
        text_length = len(text)
        
        while start < text_length:
            end = start + max_length
            if end >= text_length:
                parts.append(text[start:])
                break
            
            # Ищем оптимальное место для разрыва
            for separator in ['\n', ' ', '.', ',', ';', '!', '?']:
                break_pos = text.rfind(separator, start, end)
                if break_pos != -1:
                    break
            if break_pos == -1:
                break_pos = end
            
            parts.append(text[start:break_pos].strip())
            start = break_pos
            
            # Пропускаем разделители
            while start < text_length and text[start] in [' ', '\n', '\r', '\t']:
                start += 1
        
        return parts

    @staticmethod
    def format_ai_response(thoughts, content):
        """Форматирует ответ от AI"""
        messages = []
        
        if thoughts:
            thought_parts = MessageProcessor.split_text(
                MessageProcessor.escape_html(thoughts), 
                CONFIG['MAX_HTML_LENGTH']
            )
            for i, part in enumerate(thought_parts):
                prefix = "✅ Размышления:" if i == 0 else "✅ Размышления (продолжение):"
                messages.append(f"{prefix}\n\n<i>{part}</i>")
        
        content_parts = MessageProcessor.split_text(
            MessageProcessor.escape_html(content), 
            CONFIG['MAX_HTML_LENGTH']
        )
        for i, part in enumerate(content_parts):
            if i == 0 and not thoughts:
                messages.append(f"✅ Результат:\n\n<code>{part}</code>")
            else:
                prefix = "✅ Ответ:" if i == 0 else "✅ Ответ (продолжение):"
                messages.append(f"{prefix}\n\n<code>{part}</code>")
        
        return messages


class APIHandler:
    """Класс для работы с API"""
    
    def __init__(self):
        self.client = httpx.AsyncClient(timeout=CONFIG['REQUEST_TIMEOUT'])

    async def close(self):
        """Закрывает HTTP-клиент"""
        await self.client.aclose()

    async def generate_text(self, prompt, model):
        """Генерирует текст через API"""
        response = await self.client.post(
            CONFIG['VOIDAI_TEXT_URL'],
            headers={
                'Authorization': f'Bearer {CONFIG["VOIDAI_API_KEY"]}',
                'Content-Type': 'application/json'
            },
            json={
                'model': model,
                'messages': [{'role': 'user', 'content': prompt}]
            }
        )
        return response

    async def generate_image(self, prompt):
        """Генерирует изображение через API"""
        response = await self.client.post(
            CONFIG['VOIDAI_IMAGE_URL'],
            headers={
                'Authorization': f'Bearer {CONFIG["VOIDAI_API_KEY"]}',
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
        return response


class BotHandlers:
    """Класс с обработчиками бота"""
    
    def __init__(self, api_handler):
        self.api_handler = api_handler
        self.processor = MessageProcessor()

    async def send_safe_message(self, context, chat_id, text, parse_mode='HTML', reply_markup=None):
        """Безопасно отправляет сообщение с обработкой ошибок"""
        try:
            await context.bot.send_message(
                chat_id=chat_id, 
                text=text, 
                parse_mode=parse_mode,
                reply_markup=reply_markup
            )
        except Exception as e:
            print(f"Ошибка отправки сообщения: {e}")
            # Отправляем без HTML разметки
            plain_text = text.replace('<i>', '').replace('</i>', '').replace('<code>', '').replace('</code>', '')
            parts = self.processor.split_text(plain_text, CONFIG['MAX_MESSAGE_LENGTH'])
            for part in parts:
                await context.bot.send_message(chat_id=chat_id, text=part)

    async def send_chunked_messages(self, context, chat_id, messages):
        """Отправляет разбитые на части сообщения"""
        for message in messages:
            await self.send_safe_message(context, chat_id, message)

    async def process_text_queue(self):
        """Обрабатывает очередь текстовых запросов"""
        while True:
            try:
                chat_id, prompt, model, context = await text_queue.get()
                
                await self.send_safe_message(
                    context, chat_id, 
                    f"🔄 Генерирую текст с помощью \n{MODELS.get(model, model)}..."
                )
                
                response = await self.api_handler.generate_text(prompt, model)
                
                if response.status_code == 200:
                    data = response.json()
                    text = data['choices'][0]['message']['content']
                    thoughts, content = self.processor.extract_thoughts(text)
                    messages = self.processor.format_ai_response(thoughts, content)
                    await self.send_chunked_messages(context, chat_id, messages)
                else:
                    await self.send_safe_message(
                        context, chat_id,
                        f"❌ Ошибка: {response.status_code} - {response.text}"
                    )
                
                text_queue.task_done()
                
            except Exception as e:
                print(f"Ошибка обработки текста: {e}")
                try:
                    await self.send_safe_message(context, chat_id, f"❌ Ошибка: {str(e)}")
                except:
                    pass
                text_queue.task_done()

    async def process_image_queue(self):
        """Обрабатывает очередь запросов изображений"""
        while True:
            try:
                chat_id, prompt, context = await image_queue.get()
                
                await self.send_safe_message(context, chat_id, "🎨 Генерирую изображение...")
                
                response = await self.api_handler.generate_image(prompt)
                
                if response.status_code == 200:
                    data = response.json()
                    if data.get('data') and data['data'][0].get('b64_json'):
                        b64_image = data['data'][0]['b64_json']
                        image_bytes = base64.b64decode(b64_image)
                        await context.bot.send_photo(
                            chat_id=chat_id,
                            photo=image_bytes,
                            caption=f"✅ Изображение по запросу: <b>{self.processor.escape_html(prompt)}</b>",
                            parse_mode='HTML'
                        )
                    else:
                        await self.send_safe_message(
                            context, chat_id,
                            "❌ Не удалось получить изображение из ответа API"
                        )
                else:
                    await self.send_safe_message(
                        context, chat_id,
                        f"❌ Ошибка: {response.status_code} - {response.text}"
                    )
                
                image_queue.task_done()
                
            except Exception as e:
                print(f"Ошибка обработки изображения: {e}")
                try:
                    await self.send_safe_message(context, chat_id, f"❌ Ошибка: {str(e)}")
                except:
                    pass
                image_queue.task_done()

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /start"""
        uptime = datetime.now() - bot_start_time
        hours, remainder = divmod(int(uptime.total_seconds()), 3600)
        minutes, seconds = divmod(remainder, 60)
        
        welcome_text = f"""Привет! 👋🏼 
Я - телеграм бот <b>C0D1X AI</b>, созданный для генерации текста и картинок с помощью современных ИИ. Давай начнем это делать прямо сейчас!

Все команды находятся во вкладке <b>"Меню"</b>, и я очень надеюсь, что ты прочитал <b>правила использования бота</b>! 

⚡ <b>Статус:</b> Работаю без перерывов 24/7
⏱ <b>Аптайм:</b> {hours}ч {minutes}м {seconds}с
📊 <b>Очереди:</b> Текст: {text_queue.qsize()}, Изображения: {image_queue.qsize()}"""
        
        keyboard = [
            [InlineKeyboardButton("📋 Правила", callback_data="show_rules"), 
             InlineKeyboardButton("👤 Автор", callback_data="show_author")],
            [InlineKeyboardButton("✅ Понятно", callback_data="close_start")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await self.send_safe_message(
            context, update.effective_chat.id, welcome_text, 
            reply_markup=reply_markup
        )

    async def status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /status"""
        uptime = datetime.now() - bot_start_time
        hours, remainder = divmod(int(uptime.total_seconds()), 3600)
        minutes, seconds = divmod(remainder, 60)
        
        status_text = f"""
🤖 <b>Статус C0D1X AI Bot</b>

⚡ <b>Состояние:</b> Активно работает 24/7
⏱ <b>Время работы:</b> {hours}ч {minutes}м {seconds}с
📅 <b>Запущен:</b> {bot_start_time.strftime('%d.%m.%Y %H:%M:%S')}

📊 <b>Очереди:</b>
📝 Текстовые запросы: {text_queue.qsize()}
🎨 Генерация изображений: {image_queue.qsize()}

🛠 <b>Система:</b>
✅ Keep-alive сервер: Активен
✅ Самопинг: Активен (каждые 5 мин)
✅ Анти-слип: Включен
        """
        await self.send_safe_message(context, update.effective_chat.id, status_text)

    async def rules(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /rules"""
        rules_text = """📋 <b>Правила использования бота:</b>
Т.к. запросы бота обрабатывает сервер, использущий API поставщиков нейросетей <i>(Void AI)</i>,
все запросы пользователей - общие для тарифов <b>Void AI</b>, по этому при нарушении общих правил
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
        
        await self.send_safe_message(
            context, update.effective_chat.id, rules_text,
            reply_markup=reply_markup
        )

    async def queue_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /queue"""
        status_text = f"""
📊 Статус очередей:

📝 Текстовые запросы: {text_queue.qsize()}
🎨 Генерация изображений: {image_queue.qsize()}

💡 Используйте /status для полной информации
        """
        await update.message.reply_text(status_text)

    async def select_model(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /model"""
        user_id = update.effective_user.id
        current_model = user_models.get(user_id, 'gpt-4o-mini')
        
        keyboard = []
        for model_id, model_name in MODELS.items():
            marker = "✓ " if model_id == current_model else ""
            keyboard.append([InlineKeyboardButton(f"{marker}{model_name}", callback_data=f"model:{model_id}")])
        
        await update.message.reply_text(
            f"🤖 Выберите модель для генерации текста:\n\n"
            f"Текущая модель: {MODELS.get(current_model, current_model)}",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    async def button_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик нажатий на кнопки"""
        query = update.callback_query
        await query.answer()
        
        user_id = update.effective_user.id
        data = query.data
        
        if data in ["close_rules", "close_start"]:
            await query.delete_message()
        elif data == "show_rules":
            await query.delete_message()
            await self.rules(update, context)
        elif data == "show_author":
            await query.answer("Переводим к автору...", show_alert=False)
            keyboard = [[InlineKeyboardButton("👤 Перейти к автору", url="https://t.me/C0DIX_X")]]
            await query.edit_message_text(
                text="👤 <b>Автор бота:</b>\n\nНажми на кнопку ниже, чтобы перейти к автору!",
                parse_mode='HTML',
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        elif data.startswith("model:"):
            model_id = data[6:]
            if model_id in MODELS:
                user_models[user_id] = model_id
                await query.edit_message_text(
                    f"✅ Модель изменена на: {MODELS[model_id]}\n\n"
                    f"Теперь все ваши запросы /text будут использовать эту модель."
                )
            else:
                await query.edit_message_text("❌ Неизвестная модель")

    async def generate_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /text"""
        if not context.args:
            await update.message.reply_text("❌ Пожалуйста, укажите запрос.\nПример: /text Расскажи анекдот")
            return
        
        prompt = ' '.join(context.args)
        chat_id = update.effective_chat.id
        user_id = update.effective_user.id
        model = user_models.get(user_id, 'gpt-4o-mini')
        
        await text_queue.put((chat_id, prompt, model, context))

    async def generate_image(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /image"""
        if not context.args:
            await update.message.reply_text("❌ Пожалуйста, укажите описание изображения.\nПример: /image cute cat playing")
            return
        
        prompt = ' '.join(context.args)
        chat_id = update.effective_chat.id
        
        await image_queue.put((chat_id, prompt, context))

    async def handle_invalid_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик неизвестных команд"""
        message_text = update.message.text
        
        if message_text.startswith('/'):
            error_text = (f"❌ <b>Неизвестная команда:</b> <code>{self.processor.escape_html(message_text)}</code>\n\n"
                         f"Доступные команды:\n"
                         f"• <code>/start</code> - справка и приветствие\n"
                         f"• <code>/text [запрос]</code> - генерация текста\n"
                         f"• <code>/image [описание]</code> - генерация изображения\n"
                         f"• <code>/model</code> - выбор модели\n"
                         f"• <code>/status</code> - статус бота\n"
                         f"• <code>/queue</code> - статус очередей\n"
                         f"• <code>/rules</code> - правила использования")
            await self.send_safe_message(context, update.effective_chat.id, error_text)
        else:
            help_text = ("ℹ️ Это не команда! Все команды должны начинаться с <code>/</code>\n\n"
                        "Используйте:\n"
                        "• <code>/text [запрос]</code> - для генерации текста\n"
                        "• <code>/image [описание]</code> - для генерации изображения\n"
                        "• <code>/start</code> - для справки")
            await self.send_safe_message(context, update.effective_chat.id, help_text)


async def post_init(application: Application):
    """Инициализация при запуске бота"""
    api_handler = APIHandler()
    bot_handlers = BotHandlers(api_handler)
    
    # Сохраняем обработчики в контекст бота
    application.bot_data['api_handler'] = api_handler
    application.bot_data['bot_handlers'] = bot_handlers
    
    # Запускаем keep-alive сервер
    keep_alive_server = KeepAliveServer(port=CONFIG['PORT'])
    keep_alive_server.start()
    application.bot_data['keep_alive_server'] = keep_alive_server
    
    # Запускаем самопинг
    self_pinger = SelfPinger(interval=CONFIG['SELF_PING_INTERVAL'])
    application.bot_data['self_pinger'] = self_pinger
    asyncio.create_task(self_pinger.start())
    
    # Запускаем фоновые задачи
    asyncio.create_task(bot_handlers.process_text_queue())
    asyncio.create_task(bot_handlers.process_image_queue())
    
    print("🚀 Бот запущен и готов к работе!")
    print(f"🔧 Keep-alive сервер работает на порту {CONFIG['PORT']}")
    print(f"🔄 Самопинг настроен с интервалом {CONFIG['SELF_PING_INTERVAL']} секунд")


async def post_stop(application: Application):
    """Очистка при остановке бота"""
    # Останавливаем самопинг
    self_pinger = application.bot_data.get('self_pinger')
    if self_pinger:
        self_pinger.stop()
    
    # Останавливаем keep-alive сервер
    keep_alive_server = application.bot_data.get('keep_alive_server')
    if keep_alive_server:
        keep_alive_server.stop()
    
    # Закрываем API handler
    api_handler = application.bot_data.get('api_handler')
    if api_handler:
        await api_handler.close()
    
    print("👋 Бот остановлен")


def main():
    """Основная функция запуска бота"""
    if not CONFIG['TELEGRAM_TOKEN']:
        print('❌ Ошибка: TELEGRAM_BOT_TOKEN не установлен!')
        return
    
    if not CONFIG['VOIDAI_API_KEY']:
        print('❌ Ошибка: VOIDAI_API_KEY не установлен!')
        return
    
    application = Application.builder().token(CONFIG['TELEGRAM_TOKEN']).post_init(post_init).post_stop(post_stop).build()
    
    # Получаем обработчики из контекста
    api_handler = APIHandler()
    bot_handlers = BotHandlers(api_handler)
    
    # Сохраняем в контекст приложения
    application.bot_data['api_handler'] = api_handler
    application.bot_data['bot_handlers'] = bot_handlers
    
    # Регистрируем обработчики
    handlers = [
        CommandHandler("start", bot_handlers.start),
        CommandHandler("status", bot_handlers.status),
        CommandHandler("rules", bot_handlers.rules),
        CommandHandler("queue", bot_handlers.queue_status),
        CommandHandler("model", bot_handlers.select_model),
        CommandHandler("text", bot_handlers.generate_text),
        CommandHandler("image", bot_handlers.generate_image),
        CallbackQueryHandler(bot_handlers.button_callback),
        MessageHandler(filters.TEXT, bot_handlers.handle_invalid_command)
    ]
    
    for handler in handlers:
        application.add_handler(handler)
    
    print('✅ AI Бот запущен и готов к работе 24/7!')
    print('📝 Текстовая очередь готова')
    print('🎨 Очередь изображений готова')
    print('🛡 Анти-слип система активирована')
    
    try:
        # Для Render.com используем polling с обработкой прерываний
        application.run_polling(
            drop_pending_updates=True,
            close_loop=False
        )
    except KeyboardInterrupt:
        print("👋 Бот остановлен пользователем")
    except Exception as e:
        print(f"❌ Произошла ошибка: {e}")


if __name__ == '__main__':
    main()
