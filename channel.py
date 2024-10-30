import json
import time
import asyncio
import logging
from datetime import datetime
from typing import Dict, List, Optional
from dataclasses import dataclass, asdict
from mistralai import Mistral
from pyrogram import Client
from pyrogram.types import Message

logger = logging.getLogger(__name__)

@dataclass
class MessageGroup:
    chat_id: int
    chat_title: str
    messages: List[Dict]
    responses: List[Dict]
    timestamp: float

@dataclass
class ChannelPost:
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
        self.message_groups: List[MessageGroup] = []
        self.channel_posts: List[ChannelPost] = []
        self.last_digest_time = time.time()
        self.digest_lock = asyncio.Lock()
        
    async def save_message_group(self, chat_id: int, chat_title: str, 
                               messages: List[Message], responses: List[str]):
        """Save a group of messages and their responses to the digest"""
        async with self.digest_lock:
            message_dicts = [{
                'message_id': msg.id,
                'user_id': msg.from_user.id if msg.from_user else None,
                'user_name': f"{msg.from_user.first_name} {msg.from_user.last_name or ''}" if msg.from_user else "Unknown",
                'text': msg.text or str(msg.sticker.emoji if msg.sticker else ""),
                'timestamp': msg.date.timestamp()
            } for msg in messages]
            
            response_dicts = [{
                'text': resp,
                'timestamp': time.time()
            } for resp in responses]
            
            group = MessageGroup(
                chat_id=chat_id,
                chat_title=chat_title,
                messages=message_dicts,
                responses=response_dicts,
                timestamp=time.time()
            )
            
            self.message_groups.append(group)
            logger.info(f"Saved message group from chat: {chat_title}")

    async def monitor_channel_post(self, message: Message):
        """Monitor and save channel posts"""
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
            logger.info(f"Saved post from channel: {message.chat.title}")

    def _prepare_digest_data(self) -> dict:
        """Prepare digest data in a structured format"""
        return {
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

    async def _save_digest_to_file(self, digest_data: dict):
        """Save digest data to a JSON file for backup"""
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f'digest_{timestamp}.json'
        try:
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(digest_data, f, ensure_ascii=False, indent=2)
            logger.info(f"Saved digest to file: {filename}")
        except Exception as e:
            logger.error(f"Failed to save digest to file: {e}")

    async def create_and_post_digest(self):
        """Create and post digest to the channel"""
        if not self.config['digest_channel_id']:
            logger.warning("Digest channel ID not configured")
            return

        async with self.digest_lock:
            digest_data = self._prepare_digest_data()
            
            try:
                # Get digest from Mistral
                chat_response = self.mistral.agents.complete(
                    agent_id=self.config['digest_agent_id'],
                    messages=[{
                        "role": "user",
                        "content": f"Create a digest post based on this data: {json.dumps(digest_data, ensure_ascii=False)}"
                    }]
                )
                
                digest_text = chat_response.choices[0].message.content
                
                # Post to channel
                await self.app.send_message(
                    chat_id=self.config['digest_channel_id'],
                    text=digest_text
                )
                
                # Save digest to file for backup
                await self._save_digest_to_file(digest_data)
                
                # Clear the digest data
                self.message_groups.clear()
                self.channel_posts.clear()
                self.last_digest_time = time.time()
                
                logger.info("Successfully posted digest and cleared data")
                
            except Exception as e:
                logger.error(f"Failed to create or post digest: {e}")

    async def start_digest_loop(self):
        """Start the periodic digest creation and posting loop"""
        while True:
            try:
                current_time = time.time()
                if current_time - self.last_digest_time >= self.config['digest_interval_minutes'] * 60:
                    await self.create_and_post_digest()
                await asyncio.sleep(60)  # Check every minute
            except Exception as e:
                logger.error(f"Error in digest loop: {e}")
                await asyncio.sleep(60)  # Wait before retrying

def setup(app: Client, mistral_client: Mistral, config: dict) -> DigestManager:
    digest_manager = DigestManager(app, mistral_client, config)
    asyncio.create_task(digest_manager.start_digest_loop())
    return digest_manager
