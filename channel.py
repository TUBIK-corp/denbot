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
from pyrogram.errors import ChannelPrivate

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('DigestBot')

Path('digests').mkdir(exist_ok=True)

@dataclass
class MessageGroup:
    chat_title: str
    message: str
    response: str

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
        
        logger.info("DigestManager initialized")
        
    async def save_message_group(self, chat_title: str, message: str, response: str):
        """Save a simplified message group"""
        async with self.digest_lock:
            try:
                group = MessageGroup(
                    chat_title=chat_title,
                    message=message,
                    response=response
                )
                
                self.message_groups.append(group)
                logger.info(f"Saved message from chat: {chat_title}")
                
                # Сохраняем текущее состояние
                await self._save_current_state()
            except Exception as e:
                logger.error(f"Error saving message: {e}")

    async def monitor_channel_post(self, message: Message):
        """Monitor and save channel posts with simplified data"""
        try:
            if message.chat.id not in self.config['monitored_channels']:
                return
                
            async with self.digest_lock:
                # Проверяем наличие текста
                if not message.text and not message.caption:
                    return
                    
                post = ChannelPost(
                    channel_title=message.chat.title,
                    text=message.text or message.caption
                )
                
                self.channel_posts.append(post)
                logger.info(f"Saved post from channel: {message.chat.title}")
                await self._save_current_state()
        except Exception as e:
            logger.error(f"Error monitoring channel post: {e}")

    def _prepare_digest_data(self) -> dict:
        """Prepare simplified digest data"""
        try:
            data = {
                'messages': [{
                    'chat': group.chat_title,
                    'dialog': f"User: {group.message}\nDenvot: {group.response}"
                } for group in self.message_groups],
                'channel_posts': [{
                    'channel': post.channel_title,
                    'text': post.text[:100] + '...' if len(post.text) > 100 else post.text
                } for post in self.channel_posts],
                'stats': {
                    'total_dialogs': len(self.message_groups),
                    'total_posts': len(self.channel_posts)
                }
            }
            return data
        except Exception as e:
            logger.error(f"Error preparing digest data: {e}")
            return {}

    async def _save_current_state(self):
        """Save current state to file"""
        try:
            current_state = self._prepare_digest_data()
            with open('digests/current_state.json', 'w', encoding='utf-8') as f:
                json.dump(current_state, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Error saving state: {e}")

    async def create_and_post_digest(self):
        """Create and post digest to the channel"""
        if not self.config.get('digest_channel_id'):
            logger.warning("Digest channel ID not set")
            return

        async with self.digest_lock:
            try:
                digest_data = self._prepare_digest_data()
                if not digest_data:
                    logger.warning("No digest data to process")
                    return

                logger.info("Requesting digest from Mistral...")
                
                try:
                    chat_response = self.mistral.agents.complete(
                        agent_id=self.config['digest_agent_id'],
                        messages=[{
                            "role": "user",
                            "content": f"Create a digest post based on this data: {json.dumps(digest_data, ensure_ascii=False)}"
                        }]
                    )
                    digest_text = chat_response.choices[0].message.content
                    
                    # Попытка отправить сообщение в канал
                    try:
                        await self.app.send_message(
                            chat_id=self.config['digest_channel_id'],
                            text=digest_text
                        )
                        logger.info("Posted digest to channel")
                    except ChannelPrivate:
                        logger.error("Bot doesn't have access to the digest channel!")
                    except Exception as e:
                        logger.error(f"Error posting to channel: {e}")
                    
                    # Сохраняем копию дайджеста
                    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                    with open(f'digests/digest_{timestamp}.json', 'w', encoding='utf-8') as f:
                        json.dump(digest_data, f, ensure_ascii=False, indent=2)
                    
                    # Очищаем данные
                    self.message_groups.clear()
                    self.channel_posts.clear()
                    self.last_digest_time = time.time()
                    
                except Exception as e:
                    logger.error(f"Error creating digest: {e}")
                
            except Exception as e:
                logger.error(f"Error in create_and_post_digest: {e}")

    async def start_digest_loop(self):
        """Start the periodic digest creation and posting loop"""
        logger.info("Starting digest loop...")
        while True:
            try:
                current_time = time.time()
                if current_time - self.last_digest_time >= self.config['digest_interval_minutes'] * 60:
                    await self.create_and_post_digest()
                await asyncio.sleep(60)
            except Exception as e:
                logger.error(f"Error in digest loop: {e}")
                await asyncio.sleep(60)

def setup(app: Client, mistral_client: Mistral, config: dict) -> DigestManager:
    """Setup the digest manager"""
    digest_manager = DigestManager(app, mistral_client, config)
    asyncio.create_task(digest_manager.start_digest_loop())
    return digest_manager
