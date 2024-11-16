import asyncio
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import List, Dict
from dataclasses import dataclass
from pyrogram import Client
from pyrogram.types import Message
from mistralai import Mistral

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('MemoryManager')

@dataclass
class MemoryEntry:
    content: str
    timestamp: float
    importance: int
    context: str
    chat_title: str

class MemoryManager:
    def __init__(self, app: Client, mistral_client: Mistral, config: dict):
        self.app = app
        self.mistral = mistral_client
        self.config = config
        self.memory_lock = asyncio.Lock()
        self.memory_file = Path('memory.txt')
        self.memory: List[MemoryEntry] = []
        self.load_memory()
        logger.info("MemoryManager initialized successfully")

    def load_memory(self):
        """Загружает память из файла при старте"""
        try:
            if self.memory_file.exists():
                with open(self.memory_file, 'r', encoding='utf-8') as f:
                    content = f.read()
                    if content:
                        entries = content.split('\n\n')
                        for entry in entries:
                            if not entry.strip():
                                continue
                            try:
                                lines = entry.strip().split('\n')
                                timestamp = float(lines[0].split(': ')[1])
                                importance = int(lines[1].split(': ')[1])
                                chat = lines[2].split(': ')[1]
                                context = lines[3].split(': ')[1]
                                content = lines[4].split(': ')[1]
                                
                                self.memory.append(MemoryEntry(
                                    content=content,
                                    timestamp=timestamp,
                                    importance=importance,
                                    context=context,
                                    chat_title=chat
                                ))
                            except Exception as e:
                                logger.error(f"Error parsing memory entry: {e}")
                        logger.info(f"Loaded {len(self.memory)} memory entries")
        except Exception as e:
            logger.error(f"Error loading memory: {e}")

    async def save_memory(self):
        """Сохраняет память в файл"""
        try:
            with open(self.memory_file, 'w', encoding='utf-8') as f:
                for entry in self.memory:
                    f.write(f"Timestamp: {entry.timestamp}\n")
                    f.write(f"Importance: {entry.importance}\n")
                    f.write(f"Chat: {entry.chat_title}\n")
                    f.write(f"Context: {entry.context}\n")
                    f.write(f"Content: {entry.content}\n\n")
            logger.info(f"Saved {len(self.memory)} memory entries")
        except Exception as e:
            logger.error(f"Error saving memory: {e}")

    async def process_conversation(self, messages: List[Message], bot_responses: List[str], chat_title: str):
        """Обрабатывает группу сообщений и создает новые записи в памяти"""
        async with self.memory_lock:
            try:
                conversation_data = {
                    'timestamp': datetime.now().isoformat(),
                    'chat_title': chat_title,
                    'messages': [{
                        'user_name': f"{msg.from_user.first_name} {msg.from_user.last_name or ''}" if msg.from_user else "Unknown",
                        'text': msg.text if msg.text else str(msg.sticker.emoji if msg.sticker else ""),
                        'timestamp': msg.date
                    } for msg in messages],
                    'bot_responses': bot_responses,
                    'current_memory': [
                        f"Importance: {entry.importance}\nContent: {entry.content}\nContext: {entry.context}"
                        for entry in sorted(self.memory, key=lambda x: x.importance, reverse=True)
                    ]
                }
                
                chat_response = self.mistral.agents.complete(
                    agent_id=self.config['memory_agent_id'],
                    messages=[{
                        "role": "user",
                        "content": f"Проанализируй эту беседу и выдели значимую информацию ориентируясь на структуру в промпте: {conversation_data}"
                    }]
                )
                logger.info(f"Memory response: {chat_response}")

                if not chat_response.choices or not chat_response.choices[0].message.content:
                    logger.warning("Received empty response from Mistral API")
                    return

                content = chat_response.choices[0].message.content.strip()
                if not content:
                    return
                
                memory_entries = []
                for entry in [entry.strip() for entry in content.split('\n---\n') if entry.strip()]:
                    try:
                        lines = entry.split('\n')
                        importance_line = next((line for line in lines if line.startswith('Importance:') or line.startswith('Важность:')), None)
                        content_line = next((line for line in lines if line.startswith('Content:') or line.startswith('Содержание:')), None)
                        context_line = next((line for line in lines if line.startswith('Context:') or line.startswith('Контекст:')), None)
                        
                        if importance_line and content_line:
                            importance = int(importance_line.split(': ')[1])
                            content = content_line.split(': ')[1]
                            context = context_line.split(': ')[1] if context_line else "General"

                            memory_entries.append(MemoryEntry(
                                content=content,
                                timestamp=time.time(),
                                importance=importance,
                                context=context,
                                chat_title=chat_title
                            ))
                        else:
                            logger.warning(f"Skipping invalid entry format: {entry}")
                            
                    except (IndexError, ValueError) as e:
                        logger.error(f"Error parsing memory entry: {e}\nEntry content: {entry}")
                        continue
                self.memory.extend(memory_entries)
                await self.cleanup_memory()
                await self.save_memory()
                logger.info("Memory has been replaced and saved.")
                    
            except Exception as e:
                logger.error(f"Error processing conversation: {e}")

    async def cleanup_memory(self):
        """Очищает устаревшие или неважные записи"""
        try:
            self.memory.sort(key=lambda x: (x.importance, -x.timestamp))
            
            current_time = time.time()
            filtered_memory = [
                entry for entry in self.memory
                if (current_time - entry.timestamp < 30 * 24 * 3600) or
                (entry.importance >= 7)
            ][:1000]

            unique_entries = {}
            for entry in filtered_memory:
                unique_key = (entry.content, entry.chat_title)
                if unique_key not in unique_entries:
                    unique_entries[unique_key] = entry
            
            self.memory = list(unique_entries.values())
            logger.info(f"Cleaned up memory. Current entries: {len(self.memory)}")
        except Exception as e:
            logger.error(f"Error cleaning up memory: {e}")

    def get_relevant_memory(self, context: str = None) -> str:
        """Возвращает релевантную память для текущего контекста"""
        try:
            relevant_entries = sorted(
                [entry for entry in self.memory if not context or entry.context == context],
                key=lambda x: (x.importance, -x.timestamp),
                reverse=True
            )[:10]
            
            return "\n".join([
                f"[{entry.importance}] {entry.content} (Context: {entry.context}, Chat: {entry.chat_title})"
                for entry in relevant_entries
            ])
        except Exception as e:
            logger.error(f"Error getting relevant memory: {e}")
            return ""

def setup(app: Client, mistral_client: Mistral, config: dict) -> MemoryManager:
    """Инициализирует менеджер памяти"""
    logger.info("Setting up MemoryManager...")
    return MemoryManager(app, mistral_client, config)
