import json
import time
import random
import asyncio
import logging
import re

from difflib import SequenceMatcher
from mistralai import Mistral
from pyrogram import Client, filters
from pyrogram.enums import ChatType, ChatAction
from pyrogram.raw import functions, types

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

with open('config.json', 'r', encoding='utf-8') as f:
    config = json.load(f)

client = Mistral(api_key=config['mistral_api_key'])
app = Client("my_account", api_id=config['tg_api_id'], api_hash=config['tg_api_hash'])

last_activity_time = 0
is_online = False
message_queue = asyncio.Queue()
me = None

def contains_emoji(text):
    emoji_pattern = re.compile("["
        u"\U0001F600-\U0001F64F"  # emoticons
        u"\U0001F300-\U0001F5FF"  # symbols & pictographs
        u"\U0001F680-\U0001F6FF"  # transport & map symbols
        u"\U0001F1E0-\U0001F1FF"  # flags (iOS)
        u"\U00002702-\U000027B0"
        u"\U000024C2-\U0001F251"
        "]+", flags=re.UNICODE)
    return bool(emoji_pattern.search(text))

def chat_filter_func(_, __, message):
    if message.from_user and message.from_user.username == "leomatchbot":
        return False
    if message.text and message.text.strip().lower() in ['/leo_start', '/leo_stop']:
        return False
    if config['allowed_chats'] and message.chat.id in config['allowed_chats']:
        return True
    return filters.private and (filters.text | filters.sticker | filters.animation)

async def get_chat_history(chat_id, limit, current_message_id):
    conversation = {}
    message_counter = 0
    
    async for message in app.get_chat_history(chat_id, limit=limit, offset_id=current_message_id):
        if message.text or message.sticker or message.animation:
            if message.from_user:
                name = f"{message.from_user.first_name} {message.from_user.last_name or ''}"
                user_tag = f"@{message.from_user.username}" if message.from_user.username else ""
            elif message.sender_chat:
                name = message.sender_chat.title
                user_tag = ""
            else:
                name = "Unknown"
                user_tag = ""
            
            message_type = "text"
            content = ""
            
            if message.text:
                message_type = "text"
                content = message.text
            elif message.sticker:
                message_type = "sticker"
                content = message.sticker.emoji if message.sticker.emoji else "🔸"
            elif message.animation:
                message_type = "gif"
                content = extract_gif_info(message.animation)
            
            conversation[str(message.id)] = {
                "type": message_type,
                "name": name.strip(),
                "tag": user_tag,
                "content": content
            }
            
            message_counter += 1
            if message_counter >= limit:
                break
    
    return conversation

def extract_gif_info(animation):
    if animation.file_name:
        return animation.file_name.split('.')[0]
    elif animation.file_unique_id:
        return animation.file_unique_id
    else:
        return "Unknown GIF"

async def get_response(message, chat_id, message_id, name="unknown"):
    await asyncio.sleep(0.5)
    chat_history = await get_chat_history(chat_id, config['message_memory'], message_id)
    
    if isinstance(message, str):
        content = message
    elif message.text:
        content = message.text
    elif message.sticker:
        content = message.sticker.emoji if message.sticker.emoji else "🔸"
    elif message.animation:
        gif_info = extract_gif_info(message.animation)
        content = gif_info
    else:
        content = "Unsupported message type"
    
    user_tag = f"@{message.from_user.username}" if message.from_user and message.from_user.username else ""
    
    # Add the latest message to the history
    chat_history[str(message.id)] = {
        "type": "text" if message.text else "sticker" if message.sticker else "gif" if message.animation else "unknown",
        "name": name.strip(),
        "tag": user_tag,
        "content": content
    }
    
    try:
        chat_response = client.agents.complete(
            agent_id=config['mistral_agent_id'], 
            messages=[{"role": "user", "content": json.dumps(chat_history)}],
            response_format = {
                "type": "json_object",
            }
        )
        
        response_data = chat_response.choices[0].message.content
        
        # If string response, try to parse as JSON
        if isinstance(response_data, str):
            try:
                response_data = json.loads(response_data)
            except json.JSONDecodeError:
                # Fallback for compatibility
                logger.warning("Received non-JSON response from Mistral, falling back to text mode")
                return {
                    "messages": {
                        "0": {
                            "type": "text",
                            "content": response_data,
                            "target": None
                        }
                    }
                }
        
        return response_data
    except Exception as e:
        logger.error(f"Error getting response from Mistral: {e}")
        # Return a simple error message in the expected format
        return {
            "messages": {
                "0": {
                    "type": "text",
                    "content": "Извините, произошла ошибка при обработке запроса.",
                    "target": None
                }
            }
        }

async def simulate_typing(client, chat_id, text):
    typing_speed = config['typing_speed'] 
    for i in range(0, len(text), 3):
        await client.send_chat_action(chat_id, ChatAction.TYPING)
        chunk = text[i:i+3]
        time_to_type = len(chunk) / typing_speed
        time_with_randomness = time_to_type * random.uniform(0.8, 1.2)
        await asyncio.sleep(time_with_randomness)

async def simulate_online_status():
    global is_online, last_activity_time
    while True:
        current_time = time.time()
        if is_online and current_time - last_activity_time > random.uniform(config['delay_before_offline'][0], config['delay_before_offline'][1]):
            await app.invoke(functions.account.UpdateStatus(offline=True))
            is_online = False
            logger.info("Статус: оффлайн")
        await asyncio.sleep(10)

def is_mentioned(message):
    if not message.text:
        return False
        
    bot_names = config['bot_names']
    name_match_threshold = config['name_match_threshold']
    text = re.sub(r'[^\w\s]', '', message.text).lower().split()
    for word in text:
        for name in bot_names:
            if SequenceMatcher(None, name, word).ratio() > name_match_threshold:
                logger.info(f"Имя бота найдено по проценту сходства: {name} | Процент сходства: {SequenceMatcher(None, name, word).ratio() * 100:.2f}% | Чат: {message.chat.title} | Пользователь: {message.from_user.first_name}")
                return True
    return False

async def get_all_stickers(client):
    try:
        all_stickers = await client.invoke(functions.messages.GetAllStickers(hash=0))
        sticker_sets = []

        for set in all_stickers.sets:
            full_set = await client.invoke(functions.messages.GetStickerSet(
                stickerset=types.InputStickerSetID(
                    id=set.id, access_hash=set.access_hash
                ),
                hash=0
            ))
            sticker_sets.append(full_set)

        return sticker_sets
    except Exception as e:
        logger.error(f"Ошибка при получении всех стикеров: {e}")
        return []

async def send_gif(client, chat_id, query):
    try:
        results = await client.get_inline_bot_results("gif", query)
        if results.results:
            await client.send_inline_bot_result(chat_id, results.query_id, random.choice(results.results[:5]).id)
            return True
    except Exception as e:
        logger.error(f"Ошибка при отправке GIF: {e}")
    return False

async def send_random_sticker(client, chat_id, emoji):
    try:
        if not hasattr(client, 'all_sticker_sets'):
            client.all_sticker_sets = await get_all_stickers(client)

        matching_stickers = []
        for sticker_set in client.all_sticker_sets:
            for document in sticker_set.documents:
                for attribute in document.attributes:
                    if isinstance(attribute, types.DocumentAttributeSticker):
                        if attribute.alt == emoji:
                            matching_stickers.append(document)
                            break

        if matching_stickers:
            sticker = random.choice(matching_stickers)
            await client.invoke(functions.messages.SendMedia(
                peer=await client.resolve_peer(chat_id),
                media=types.InputMediaDocument(
                    id=types.InputDocument(
                        id=sticker.id,
                        access_hash=sticker.access_hash,
                        file_reference=sticker.file_reference
                    )
                ),
                message="",
                random_id=random.randint(1, 2147483647)
            ))
            return True
        else:
            logger.warning(f"Не найдено подходящих стикеров для эмодзи: {emoji}")
            return False
    except Exception as e:
        logger.error(f"Ошибка при отправке стикера: {e}")
        return False

@app.on_message(filters.create(chat_filter_func) & ~(filters.channel))
async def auto_reply(client, message):
    await message_queue.put([client, message])

async def process_queue():
    global is_online, last_activity_time
    message_groups = {}
    last_ping_time = {}
    ping_timeout = 10
    
    while True:
        try:
            client, message = await message_queue.get()
            chat_id = message.chat.id
            current_time = time.time()
            
            is_direct_interaction = (
                (message.reply_to_message and message.reply_to_message.from_user and message.reply_to_message.from_user.is_self) or 
                message.chat.type == ChatType.PRIVATE or 
                is_mentioned(message)
            )
            
            if is_direct_interaction or (
                chat_id in last_ping_time and 
                current_time - last_ping_time[chat_id] < ping_timeout
            ):
                if is_direct_interaction:
                    last_ping_time[chat_id] = current_time

                if not is_online:
                    await asyncio.sleep(random.uniform(config['delay_before_online'][0], config['delay_before_online'][1]))
                    await app.invoke(functions.account.UpdateStatus(offline=False))
                    is_online = True
                    logger.info("Статус: онлайн")
                last_activity_time = current_time
                
                await client.read_chat_history(chat_id)                

                if chat_id not in message_groups:
                    message_groups[chat_id] = {
                        'messages': [],
                        'timer': None
                    }
                
                message_groups[chat_id]['messages'].append((client, message))
                
                if message_groups[chat_id]['timer'] is not None:
                    message_groups[chat_id]['timer'].cancel()
                
                async def process_message_group(chat_id):
                    await asyncio.sleep(10)  # Ждём 10 секунд для группировки
                    
                    if chat_id in message_groups:
                        last_client, last_message = message_groups[chat_id]['messages'][-1]
                        
                        content_type = "text" if last_message.text else "sticker" if last_message.sticker else "GIF" if last_message.animation else "unknown"
                        content = last_message.text or last_message.caption or (last_message.sticker.emoji if last_message.sticker else (extract_gif_info(last_message.animation) if last_message.animation else "unknown"))
                        chat_title = last_message.chat.title or "Unknown Chat"
                        user_first_name = last_message.from_user.first_name if last_message.from_user and last_message.from_user.first_name else "Unknown"
                        user_last_name = last_message.from_user.last_name if last_message.from_user and last_message.from_user.last_name else ""
                        user_username = message.from_user.username if message.from_user and message.from_user.username else "Unknown"
                        
                        logger.info(f"Обработка группы сообщений. Последнее сообщение: {content_type}: {content} | Чат: {chat_title} | Пользователь: {user_username}")
                        
                        response_data = await get_response(
                            message=last_message,
                            chat_id=chat_id,
                            message_id=last_message.id,
                            name=f"{user_first_name} {user_last_name}".strip()
                        )
                        
                        logger.info(f"Response data: {response_data}")
                        
                        try:
                            if "messages" in response_data:
                                for idx, msg_data in response_data["messages"].items():
                                    msg_type = msg_data.get("type", "text")
                                    msg_content = msg_data.get("content", "")
                                    msg_target = msg_data.get("target")
                                    
                                    target_message = None
                                    if msg_target:
                                        try:
                                            # Try to find the target message to reply to
                                            for client_msg, orig_msg in message_groups[chat_id]['messages']:
                                                if str(orig_msg.id) == str(msg_target):
                                                    target_message = orig_msg
                                                    break
                                        except Exception as e:
                                            logger.error(f"Error finding target message: {e}")
                                    
                                    if not target_message:
                                        target_message = last_message
                                    
                                    if msg_type == "text" and msg_content:
                                        await simulate_typing(last_client, chat_id, msg_content)
                                        await target_message.reply(msg_content)
                                    elif msg_type == "gif" and msg_content:
                                        await send_gif(last_client, chat_id, msg_content)
                                    elif msg_type == "sticker" and msg_content:
                                        await send_random_sticker(last_client, chat_id, msg_content)
                            else:
                                logger.error("Invalid response format from Mistral")
                        except Exception as e:
                            logger.error(f"Error processing response: {e}")
                            await last_message.reply("Произошла ошибка при обработке ответа.")
                        
                        del message_groups[chat_id]
                
                timer = asyncio.create_task(process_message_group(chat_id))
                message_groups[chat_id]['timer'] = timer
            else: 
                logger.info(f"Сообщение проигнорировано: {message.text or 'Не текстовое сообщение'} | Чат: {(message.chat.title if message.chat else 'Unknown Chat')} | Пользователь: {(message.from_user.username if message.from_user else 'Unknown')}")
        except Exception as e:
            logger.error(f"Ошибка при обработке сообщения: {e}", exc_info=True)
        finally:
            message_queue.task_done()

async def main():
    global me
    logger.info("Starting bot...")
    await app.start()
    me = await app.get_me()
    logger.info(f"Bot started as {me.first_name} {me.last_name} (@{me.username})")
    await app.invoke(functions.account.UpdateStatus(offline=True))
    logger.info("Status set to offline")
    asyncio.create_task(process_queue())
    await simulate_online_status()

if __name__ == "__main__":
    app.run(main())
