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

# Настройка детального логирования
logging.basicConfig(
    level=logging.DEBUG,  # Изменено на DEBUG для более подробных логов
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('digest_bot_debug.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('DigestBot')

# Остальные классы остаются теми же, изменяем только методы с добавлением отладки

class DigestManager:
    def __init__(self, app: Client, mistral_client: Mistral, config: dict):
        self.app = app
        self.mistral = mistral_client
        self.config = config
        self.message_groups: List[MessageGroup] = []
        self.channel_posts: List[ChannelPost] = []
        self.last_digest_time = time.time()
        self.digest_lock = asyncio.Lock()
        
        logger.debug(f"DigestManager initialized with config: {json.dumps(config, indent=2)}")
        self._validate_config()

    def _validate_config(self):
        """Проверяем наличие всех необходимых параметров в конфиге"""
        required_fields = [
            'digest_agent_id',
            'monitored_channels',
            'digest_channel_id',
            'digest_interval_minutes'
        ]
        
        for field in required_fields:
            value = self.config.get(field)
            logger.debug(f"Config field '{field}': {value}")
            if not value and value != 0:  # Проверяем, что значение не пустое и не None
                logger.error(f"Missing required config field: {field}")

    async def create_and_post_digest(self):
        """Create and post digest to the channel"""
        logger.debug("Starting create_and_post_digest method")
        
        if not self.config['digest_channel_id']:
            logger.error("Digest channel ID not configured")
            return

        async with self.digest_lock:
            try:
                logger.debug("Preparing digest data...")
                digest_data = self._prepare_digest_data()
                if not digest_data:
                    logger.warning("No digest data to process")
                    return

                logger.debug(f"Current digest data: {json.dumps(digest_data, indent=2)}")

                try:
                    logger.debug("Requesting digest from Mistral...")
                    chat_response = self.mistral.agents.complete(
                        agent_id=self.config['digest_agent_id'],
                        messages=[{
                            "role": "user",
                            "content": f"Create a digest post based on this data: {json.dumps(digest_data, ensure_ascii=False)}"
                        }]
                    )
                    logger.debug("Received response from Mistral")
                except Exception as e:
                    logger.error(f"Error while requesting digest from Mistral: {e}", exc_info=True)
                    return

                digest_text = chat_response.choices[0].message.content
                logger.debug(f"Generated digest text: {digest_text}")

                try:
                    logger.debug(f"Attempting to send message to channel {self.config['digest_channel_id']}")
                    message = await self.app.send_message(
                        chat_id=self.config['digest_channel_id'],
                        text=digest_text
                    )
                    logger.info(f"Successfully posted digest to channel: {message.link}")
                except Exception as e:
                    logger.error(f"Error while sending message to channel: {e}", exc_info=True)
                    return

                await self._save_digest_to_file(digest_data)
                
                self.message_groups.clear()
                self.channel_posts.clear()
                self.last_digest_time = time.time()
                logger.debug("Digest cycle completed successfully")
                
            except Exception as e:
                logger.error(f"Error in create_and_post_digest: {e}", exc_info=True)

    async def start_digest_loop(self):
        """Start the periodic digest creation and posting loop"""
        logger.info("Starting digest loop...")
        while True:
            try:
                current_time = time.time()
                elapsed_minutes = (current_time - self.last_digest_time) / 60
                logger.debug(f"Checking digest time - Elapsed minutes: {elapsed_minutes:.2f}")
                
                if elapsed_minutes >= self.config['digest_interval_minutes']:
                    logger.info(f"Time for digest (elapsed: {elapsed_minutes:.2f} minutes)")
                    await self.create_and_post_digest()
                else:
                    logger.debug(f"Waiting for next digest. {self.config['digest_interval_minutes'] - elapsed_minutes:.2f} minutes remaining")
                
                await asyncio.sleep(60)
            except Exception as e:
                logger.error(f"Error in digest loop: {e}", exc_info=True)
                await asyncio.sleep(60)

def setup(app: Client, mistral_client: Mistral, config: dict) -> DigestManager:
    """Setup the digest manager and start the digest loop"""
    logger.info("Setting up DigestManager...")
    try:
        digest_manager = DigestManager(app, mistral_client, config)
        asyncio.create_task(digest_manager.start_digest_loop())
        logger.info("DigestManager setup completed successfully")
        return digest_manager
    except Exception as e:
        logger.error(f"Error during DigestManager setup: {e}", exc_info=True)
        raise
    