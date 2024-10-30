import json
import time
import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict
from mistralai import Mistral
from pyrogram import Client
from pyrogram.types import Message

# Настройка логирования
class DigestLogger:
    def __init__(self):
        # Создаём директорию для логов если её нет
        Path('logs').mkdir(exist_ok=True)
        
        # Основной логгер
        self.logger = logging.getLogger('DigestBot')
        self.logger.setLevel(logging.INFO)
        
        # Форматтер для логов
        formatter = logging.Formatter(
            '%(asctime)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        
        # Хендлер для консоли
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        self.logger.addHandler(console_handler)
        
        # Хендлер для файла
        file_handler = logging.FileHandler(
            f'logs/digest_bot_{datetime.now().strftime("%Y%m%d")}.log',
            encoding='utf-8'
        )
        file_handler.setFormatter(formatter)
        self.logger.addHandler(file_handler)

log = DigestLogger().logger

@dataclass
class Message:
    """Структура для хранения сообщения"""
    message_id: int
    user_id: Optional[int]
    user_name: str
    text: str
    timestamp: float
    message_type: str  # 'text', 'sticker', 'animation', etc.
    chat_id: int
    chat_title: str

@dataclass
class Response:
    """Структура для хранения ответа бота"""
    text: str
    timestamp: float
    message_id: Optional[int] = None

@dataclass
class Conversation:
    """Структура для хранения группы сообщений и ответов"""
    chat_id: int
    chat_title: str
    messages: List[Message]
    responses: List[Response]
    timestamp: float

@dataclass
class ChannelPost:
    """Структура для хранения поста из канала"""
    channel_id: int
    channel_title: str
    post_id: int
    text: str
    timestamp: float
    views: Optional[int] = None
    forwards: Optional[int] = None

class DigestManager:
    def __init__(self, app: Client, mistral_client: Mistral, config: dict):
        self.app = app
        self.mistral = mistral_client
        self.config = config
        self.conversations: List[Conversation] = []
        self.channel_posts: List[ChannelPost] = []
        self.last_digest_time = time.time()
        self.digest_lock = asyncio.Lock()
        
        # Создаём необходимые директории
        self.digest_dir = Path('digests')
        self.digest_dir.mkdir(exist_ok=True)
        
        # Загружаем последнее состояние если оно есть
        self._load_last_state()
        
        log.info("DigestManager initialized")
        self._log_config()

    def _log_config(self):
        """Логируем текущую конфигурацию"""
        log.info("Current configuration:")
        log.info(f"- Monitored channels: {len(self.config['monitored_channels'])}")
        log.info(f"- Digest interval: {self.config['digest_interval_minutes']} minutes")
        log.info(f"- Digest channel: {self.config['digest_channel_id']}")
        log.info(f"- Detailed logging: {self.config['digest_settings']['detailed_logging']}")

    def _load_last_state(self):
        """Загружаем последнее сохранённое состояние"""
        try:
            state_file = self.digest_dir / 'current_state.json'
            if state_file.exists():
                with state_file.open('r', encoding='utf-8') as f:
                    data = json.load(f)
                    log.info(f"Loaded last state: {len(data.get('conversations', []))} conversations")
        except Exception as e:
            log.error(f"Failed to load last state: {e}")

    async def save_conversation(self, message: Message, response: str):
        """Сохраняем сообщение и ответ"""
        async with self.digest_lock:
            try:
                msg = Message(
                    message_id=message.id,
                    user_id=message.from_user.id if message.from_user else None,
                    user_name=f"{message.from_user.first_name} {message.from_user.last_name or ''}" if message.from_user else "Unknown",
                    text=message.text or str(message.sticker.emoji if message.sticker else ""),
                    timestamp=message.date.timestamp(),
                    message_type=self._get_message_type(message),
                    chat_id=message.chat.id,
                    chat_title=message.chat.title or "Unknown Chat"
                )
                
                resp = Response(
                    text=response,
                    timestamp=time.time()
                )

                # Ищем существующую беседу или создаём новую
                for conv in self.conversations:
                    if conv.chat_id == message.chat.id:
                        conv.messages.append(msg)
                        conv.responses.append(resp)
                        break
                else:
                    self.conversations.append(Conversation(
                        chat_id=message.chat.id,
                        chat_title=message.chat.title or "Unknown Chat",
                        messages=[msg],
                        responses=[resp],
                        timestamp=time.time()
                    ))

                if self.config['digest_settings']['detailed_logging']:
                    log.info(f"Saved conversation in chat: {message.chat.title} "
                            f"(Total: {len(self.conversations)})")
                
                await self._save_current_state()
                
            except Exception as e:
                log.error(f"Error saving conversation: {e}", exc_info=True)

    def _get_message_type(self, message: Message) -> str:
        """Определяем тип сообщения"""
        if message.text:
            return 'text'
        elif message.sticker:
            return 'sticker'
        elif message.animation:
            return 'animation'
        else:
            return 'unknown'

    async def monitor_channel_post(self, message: Message):
        """Мониторим посты в каналах"""
        try:
            if message.chat.id not in self.config['monitored_channels']:
                return
                
            async with self.digest_lock:
                post = ChannelPost(
                    channel_id=message.chat.id,
                    channel_title=message.chat.title,
                    post_id=message.id,
                    text=message.text or "",
                    timestamp=message.date.timestamp(),
                    views=message.views,
                    forwards=message.forwards
                )
                
                self.channel_posts.append(post)
                
                if self.config['digest_settings']['detailed_logging']:
                    log.info(f"Saved post from channel: {message.chat.title} "
                            f"(Total posts: {len(self.channel_posts)})")
                
                await self._save_current_state()
                
        except Exception as e:
            log.error(f"Error monitoring channel post: {e}", exc_info=True)

    async def _save_current_state(self):
        """Сохраняем текущее состояние в файл"""
        try:
            current_state = {
                'timestamp': datetime.now().isoformat(),
                'conversations': [asdict(conv) for conv in self.conversations],
                'channel_posts': [asdict(post) for post in self.channel_posts]
            }
            
            state_file = self.digest_dir / 'current_state.json'
            with state_file.open('w', encoding='utf-8') as f:
                json.dump(current_state, f, ensure_ascii=False, indent=2)
                
            if self.config['digest_settings']['detailed_logging']:
                log.info("Current state saved successfully")
                
        except Exception as e:
            log.error(f"Error saving current state: {e}", exc_info=True)

    async def create_and_post_digest(self):
        """Создаём и публикуем сводку"""
        if not self.config['digest_channel_id']:
            log.warning("Digest channel ID not configured!")
            return

        async with self.digest_lock:
            try:
                # Проверяем минимальное количество сообщений
                total_messages = sum(len(conv.messages) for conv in self.conversations)
                if total_messages < self.config['digest_settings']['min_messages_for_digest']:
                    log.info(f"Not enough messages for digest. Current: {total_messages}, "
                            f"Required: {self.config['digest_settings']['min_messages_for_digest']}")
                    return

                # Готовим данные для сводки
                digest_data = {
                    'timestamp': datetime.now().isoformat(),
                    'period_minutes': self.config['digest_interval_minutes'],
                    'conversations': [asdict(conv) for conv in self.conversations],
                    'channel_posts': [asdict(post) for post in self.channel_posts],
                    'stats': {
                        'total_chats': len({conv.chat_id for conv in self.conversations}),
                        'total_messages': total_messages,
                        'total_responses': sum(len(conv.responses) for conv in self.conversations),
                        'total_channel_posts': len(self.channel_posts)
                    }
                }

                log.info("Requesting digest from Mistral...")
                chat_response = self.mistral.agents.complete(
                    agent_id=self.config['digest_agent_id'],
                    messages=[{
                        "role": "user",
                        "content": f"Create a digest post based on this data: {json.dumps(digest_data, ensure_ascii=False)}"
                    }]
                )
                
                digest_text = chat_response.choices[0].message.content
                log.info("Received digest from Mistral")

                # Публикуем в канал
                message = await self.app.send_message(
                    chat_id=self.config['digest_channel_id'],
                    text=digest_text
                )
                log.info(f"Posted digest to channel: {message.link}")
                
                # Сохраняем сводку в файл
                if self.config['digest_settings']['save_files']:
                    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                    digest_file = self.digest_dir / f'digest_{timestamp}.json'
                    with digest_file.open('w', encoding='utf-8') as f:
                        json.dump(digest_data, f, ensure_ascii=False, indent=2)
                    log.info(f"Saved digest to file: {digest_file}")
                    
                    # Обновляем последнюю сводку
                    latest_file = self.digest_dir / 'latest_digest.json'
                    with latest_file.open('w', encoding='utf-8') as f:
                        json.dump(digest_data, f, ensure_ascii=False, indent=2)
                
                # Очищаем данные
                self.conversations.clear()
                self.channel_posts.clear()
                self.last_digest_time = time.time()
                
                log.info("Digest created and posted successfully")
                
            except Exception as e:
                log.error(f"Failed to create or post digest: {e}", exc_info=True)

    async def start_digest_loop(self):
        """Запускаем цикл создания сводок"""
        log.info("Starting digest loop...")
        while True:
            try:
                current_time = time.time()
                elapsed_minutes = (current_time - self.last_digest_time) / 60
                
                if elapsed_minutes >= self.config['digest_interval_minutes']:
                    log.info(f"Time for digest (elapsed: {elapsed_minutes:.2f} minutes)")
                    await self.create_and_post_digest()
                
                await asyncio.sleep(60)  # Проверяем каждую минуту
                
            except Exception as e:
                log.error(f"Error in digest loop: {e}", exc_info=True)
                await asyncio.sleep(60)

def setup(app: Client, mistral_client: Mistral, config: dict) -> DigestManager:
    """Настраиваем и запускаем DigestManager"""
    log.info("Setting up DigestManager...")
    digest_manager = DigestManager(app, mistral_client, config)
    asyncio.create_task(digest_manager.start_digest_loop())
    return digest_manager
