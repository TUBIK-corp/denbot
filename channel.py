import json
import time
import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass, asdict
from mistralai import Mistral
from pyrogram import Client
from pyrogram.types import Message

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('DigestBot')

# Создаем директорию для сводок если её нет
Path('digests').mkdir(exist_ok=True)

@dataclass
class MessageGroup:
    chat_title: str
    messages: List[Dict]
    responses: List[Dict]

@dataclass
class ChannelPost:
    channel_title: str
    text: str

class DigestManager:
    def __init__(self, app: Client, mistral_client: Mistral, config: dict):
        self.app = app
        self.mistral = mistral_client
        self.config = config
        self.message_groups: List[MessageGroup] = []
        self.channel_posts: List[ChannelPost] = []
        self.last_digest_time = time.time()
        self.digest_lock = asyncio.Lock()
        
        # Проверяем конфигурацию
        self._validate_config()
        logger.info("DigestManager initialized successfully")
        logger.info(f"Monitoring {len(config['monitored_channels'])} channels")
        logger.info(f"Digest interval set to {config['digest_interval_minutes']} minutes")

    def _validate_config(self):
        """Проверяем наличие всех необходимых параметров в конфиге"""
        required_fields = [
            'digest_agent_id',
            'monitored_channels',
            'digest_channel_id',
            'digest_interval_minutes'
        ]
        missing_fields = [field for field in required_fields if not self.config.get(field)]
        if missing_fields:
            logger.warning(f"Missing config fields: {', '.join(missing_fields)}")

    async def save_message_group(self, chat_id: int, chat_title: str, 
                               messages: List[Message], responses: List[str]):
        """Save a group of messages and their responses to the digest"""
        async with self.digest_lock:
            try:
                message_dicts = [{
                    'user_name': f"{msg.from_user.first_name} {msg.from_user.last_name or ''}" if msg.from_user else "Unknown",
                    'text': msg.text or str(msg.sticker.emoji if msg.sticker else "")
                } for msg in messages]
                
                response_dicts = [{
                    'text': resp
                } for resp in responses]
                
                group = MessageGroup(
                    chat_title=chat_title,
                    messages=message_dicts,
                    responses=response_dicts
                )
                
                self.message_groups.append(group)
                logger.info(f"Saved message group from chat: {chat_title} (Total groups: {len(self.message_groups)})")
                
                # Сохраняем текущее состояние в файл
                await self._save_current_state()
            except Exception as e:
                logger.error(f"Error saving message group: {e}")

    async def monitor_channel_post(self, message: Message):
        """Monitor and save channel posts"""
        try:
            if message.chat.id not in self.config['monitored_channels']:
                return
                
            async with self.digest_lock:
                post = ChannelPost(
                    channel_title=message.chat.title,
                    text=message.text or ""
                )
                
                self.channel_posts.append(post)
                logger.info(f"Saved post from channel: {message.chat.title} (Total posts: {len(self.channel_posts)})")
                
                # Сохраняем текущее состояние в файл
                await self._save_current_state()
        except Exception as e:
            logger.error(f"Error monitoring channel post: {e}")

    def _prepare_digest_data(self) -> dict:
        """Prepare digest data in a structured format"""
        try:
            data = {
                'timestamp': datetime.now().isoformat(),
                'period_minutes': self.config['digest_interval_minutes'],
                'message_groups': [asdict(group) for group in self.message_groups],
                'channel_posts': [asdict(post) for post in self.channel_posts],
                'stats': {
                    'total_chats': len({group.chat_id for group in self.message_groups}),
                    'total_messages': sum(len(group.messages) for group in self.message_groups),
                    'total_responses': sum(len(group.responses) for group in self.message_groups),
                    'total_channel_posts': len(self.channel_posts),
                    'monitored_channels': len(self.config['monitored_channels'])
                }
            }
            logger.info(f"Prepared digest data: {json.dumps(data['stats'], indent=2)}")
            return data
        except Exception as e:
            logger.error(f"Error preparing digest data: {e}")
            return {}

    async def _save_current_state(self):
        """Save current state to a file for monitoring"""
        try:
            current_state = self._prepare_digest_data()
            state_file = Path('digests/current_state.json')
            with state_file.open('w', encoding='utf-8') as f:
                json.dump(current_state, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Error saving current state: {e}")

    async def _save_digest_to_file(self, digest_data: dict):
        """Save digest data to a JSON file for backup"""
        try:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f'digests/digest_{timestamp}.json'
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(digest_data, f, ensure_ascii=False, indent=2)
            logger.info(f"Saved digest to file: {filename}")
            
            # Также сохраняем последнюю сводку отдельно
            with open('digests/latest_digest.json', 'w', encoding='utf-8') as f:
                json.dump(digest_data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Failed to save digest to file: {e}")

    async def create_and_post_digest(self):
        """Create and post digest to the channel"""
        if not self.config['digest_channel_id']:
            logger.warning("Digest channel ID not configured")
            return

        async with self.digest_lock:
            try:
                digest_data = self._prepare_digest_data()
                if not digest_data:
                    logger.warning("No digest data to process")
                    return

                logger.info("Requesting digest from Mistral...")
                chat_response = self.mistral.agents.complete(
                    agent_id=self.config['digest_agent_id'],
                    messages=[{
                        "role": "user",
                        "content": f"Create a digest post based on this data: {json.dumps(digest_data, ensure_ascii=False)}"
                    }]
                )
                
                digest_text = chat_response.choices[0].message.content
                logger.info("Received digest from Mistral")
                logger.info(f"Digest preview: {digest_text[:200]}...")

                # Post to channel
                message = await self.app.send_message(
                    chat_id=self.config['digest_channel_id'],
                    text=digest_text
                )
                logger.info(f"Posted digest to channel: {message.link}")
                
                # Save digest to file for backup
                await self._save_digest_to_file(digest_data)
                
                # Clear the digest data
                self.message_groups.clear()
                self.channel_posts.clear()
                self.last_digest_time = time.time()
                
                logger.info("Successfully posted digest and cleared data")
                
            except Exception as e:
                logger.error(f"Failed to create or post digest: {e}", exc_info=True)

    async def start_digest_loop(self):
        """Start the periodic digest creation and posting loop"""
        logger.info("Starting digest loop...")
        while True:
            try:
                current_time = time.time()
                elapsed_minutes = (current_time - self.last_digest_time) / 60
                
                if elapsed_minutes >= self.config['digest_interval_minutes']:
                    logger.info(f"Time for digest (elapsed: {elapsed_minutes:.2f} minutes)")
                    await self.create_and_post_digest()
                
                await asyncio.sleep(60)  # Check every minute
            except Exception as e:
                logger.error(f"Error in digest loop: {e}", exc_info=True)
                await asyncio.sleep(60)  # Wait before retrying

def setup(app: Client, mistral_client: Mistral, config: dict) -> DigestManager:
    """Setup the digest manager and start the digest loop"""
    logger.info("Setting up DigestManager...")
    digest_manager = DigestManager(app, mistral_client, config)
    asyncio.create_task(digest_manager.start_digest_loop())
    return digest_manager
