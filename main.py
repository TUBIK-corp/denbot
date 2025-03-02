import json
import time
import random
import asyncio
import logging
import re
import base64
import io

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
    return filters.private and (filters.text | filters.sticker | filters.animation | filters.photo)

async def get_chat_history(chat_id, limit, current_message_id):
    conversation = {}
    message_counter = 0
    
    async for message in app.get_chat_history(chat_id, limit=limit, offset_id=current_message_id):
        if message.text or message.sticker or message.animation or message.photo:
            is_bot_message = message.from_user and message.from_user.is_self
            
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
            elif message.caption:
                message_type = "text"
                content = message.caption
            elif message.sticker:
                message_type = "sticker"
                content = message.sticker.emoji if message.sticker.emoji else "🔸"
            elif message.animation:
                message_type = "gif"
                content = extract_gif_info(message.animation)
            elif message.photo:
                message_type = "photo"
                # Simply note that there was a photo without analyzing it
                content = "[Image was shared]" + (message.caption or "")
            
            # Add reply_to information
            target = None
            if message.reply_to_message_id:
                target = str(message.reply_to_message_id)
            
            conversation[str(message.id)] = {
                "type": message_type,
                "name": name.strip(),
                "tag": user_tag,
                "content": content,
                "role": "assistant" if is_bot_message else "user",
                "target": target
            }
            
            message_counter += 1
            if message_counter >= limit:
                break
    return conversation

async def analyze_image(photo):
    try:
        # Get the photo file_id (Pyrogram stores photos as a MediaGroup)
        if hasattr(photo, 'file_id'):  # Single photo object
            file_id = photo.file_id
        else:  # MediaGroup or list of PhotoSize objects
            # Get the largest photo size (last item in the list)
            if isinstance(photo, list) and len(photo) > 0:
                file_id = photo[-1].file_id
            else:
                logger.error(f"Unexpected photo format: {type(photo)}")
                return "Unable to analyze image: invalid format"
        
        # Download the photo
        file_path = await app.download_media(file_id, file_name="temp.jpg")
        
        # Encode the image to base64
        with open(file_path, "rb") as image_file:
            image_base64 = base64.b64encode(image_file.read()).decode('utf-8')
        
        # Define the messages for the image analysis
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Analyze this image and provide a detailed description."},
                    {"type": "image_url", "image_url": f"data:image/jpeg;base64,{image_base64}"}
                ]
            }
        ]
        
        # Get the chat response
        chat_response = client.chat.complete(
            model=config.get('mistral_vision_model', 'pixtral-12b-2409'),
            messages=messages
        )
        
        image_description = chat_response.choices[0].message.content
        logger.info(f"Image description: {image_description[:100]}...")
        
        # Clean up the temporary file
        import os
        os.remove(file_path)
        
        return image_description
    except Exception as e:
        logger.error(f"Error analyzing image: {e}")
        return "Unable to analyze image"

def extract_gif_info(animation):
    if animation.file_name:
        return animation.file_name.split('.')[0]
    elif animation.file_unique_id:
        return animation.file_unique_id
    else:
        return "Unknown GIF"

async def get_response(message, chat_id, message_id, name="unknown", group_messages=None):
    await asyncio.sleep(0.5)
    # Получаем историю чата
    chat_history = await get_chat_history(chat_id, config['message_memory'], message_id)
    
    content = ""
    
    if isinstance(message, str):
        content = message
    elif hasattr(message, '_text') and message._text:
        content = message._text
    elif hasattr(message, '_caption') and message._caption:
        content = message._caption
    elif message.text:
        content = message.text
    elif message.caption:
        content = message.caption
    elif message.sticker:
        content = message.sticker.emoji if message.sticker.emoji else "🔸"
    elif message.animation:
        gif_info = extract_gif_info(message.animation)
        content = gif_info
    else:
        content = ""
    
    # Анализируем изображение, если оно не было проанализировано ранее
    if hasattr(message, 'photo') and message.photo and not (hasattr(message, '_text') or hasattr(message, '_caption')):
        try:
            image_description = await analyze_image(message.photo)
            if content:
                content = f"[Image: {image_description}] {content}"
            else:
                content = f"[Image: {image_description}]"
        except Exception as e:
            logger.error(f"Error processing image in response: {e}")
            content = "[Image: Unable to analyze]" + (content if content else "")
    
    user_tag = f"@{message.from_user.username}" if message.from_user and message.from_user.username else ""
    
    # Add the latest message to the history
    message_type = "text"
    if message.photo:
        message_type = "photo"
    elif message.sticker:
        message_type = "sticker"
    elif message.animation:
        message_type = "gif"
    
    # Get reply_to information
    target = None
    if hasattr(message, 'reply_to_message') and message.reply_to_message:
        target = str(message.reply_to_message.id)
    elif hasattr(message, 'reply_to_message_id') and message.reply_to_message_id:
        target = str(message.reply_to_message_id)
    
    chat_history[str(message.id)] = {
        "type": message_type,
        "name": name.strip(),
        "tag": user_tag,
        "content": content,
        "role": "user",
        "target": target
    }
    
    # Если у нас есть группа сообщений, добавляем их все в историю чата
    if group_messages:
        for client_msg, msg in group_messages:
            if msg.id != message.id:  # Не дублируем последнее сообщение
                msg_content = ""
                
                if hasattr(msg, '_text') and msg._text:
                    msg_content = msg._text
                elif hasattr(msg, '_caption') and msg._caption:
                    msg_content = msg._caption
                elif msg.text:
                    msg_content = msg.text
                elif msg.caption:
                    msg_content = msg.caption
                elif msg.sticker:
                    msg_content = msg.sticker.emoji if msg.sticker.emoji else "🔸"
                elif msg.animation:
                    msg_content = extract_gif_info(msg.animation)
                
                # Анализируем изображение, если оно не было проанализировано ранее
                if hasattr(msg, 'photo') and msg.photo and not (hasattr(msg, '_text') or hasattr(msg, '_caption')):
                    try:
                        image_description = await analyze_image(msg.photo)
                        if msg_content:
                            msg_content = f"[Image: {image_description}] {msg_content}"
                        else:
                            msg_content = f"[Image: {image_description}]"
                    except Exception as e:
                        logger.error(f"Error processing group message image: {e}")
                
                msg_type = "text"
                if msg.photo:
                    msg_type = "photo"
                elif msg.sticker:
                    msg_type = "sticker"
                elif msg.animation:
                    msg_type = "gif"
                
                msg_user_tag = f"@{msg.from_user.username}" if msg.from_user and msg.from_user.username else ""
                msg_name = f"{msg.from_user.first_name} {msg.from_user.last_name or ''}".strip() if msg.from_user else "Unknown"
                
                # Get reply_to information for this message
                msg_target = None
                if hasattr(msg, 'reply_to_message') and msg.reply_to_message:
                    msg_target = str(msg.reply_to_message.id)
                elif hasattr(msg, 'reply_to_message_id') and msg.reply_to_message_id:
                    msg_target = str(msg.reply_to_message_id)
                
                chat_history[str(msg.id)] = {
                    "type": msg_type,
                    "name": msg_name,
                    "tag": msg_user_tag,
                    "content": msg_content,
                    "role": "user",
                    "target": msg_target
                }
    
    try:
        formatted_messages = format_chat_history_for_mistral(chat_history)

        chat_response = client.agents.complete(
            agent_id=config['mistral_agent_id'], 
            messages=formatted_messages,
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

def format_chat_history_for_mistral(chat_history):
    # Sort messages by ID (chronological order)
    sorted_messages = sorted(chat_history.items(), key=lambda x: int(x[0]), reverse=False)
    formatted_messages = []
    
    logger.info("Messages order after sorting:")
    for idx, (msg_id, msg_data) in enumerate(sorted_messages):
        logger.info(f"{idx+1}. ID: {msg_id}, Target: {msg_data.get('target', 'None')}, Content: {msg_data['content'][:30]}...")
        role = "user" if msg_data.get("role", "user") == "user" else "assistant"

        content = {
            "id": f'{msg_id}',
            "name": msg_data["name"],
            "tag": msg_data["tag"],
            "content": msg_data["content"],
            "target": msg_data.get("target", None)
        }

        formatted_messages.append({
            "role": role,
            "content": f'"{msg_id}": {content}'
        })
    
    return formatted_messages

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
    if not message.text and not message.caption:
        return False
    
    text_to_check = message.text or message.caption    
    bot_names = [name.lower() for name in config['bot_names']]
    name_match_threshold = config['name_match_threshold']
    
    # First check for exact matches (including @ mentions)
    text_lower = text_to_check.lower()
    for name in bot_names:
        if name.lower() in text_lower:
            logger.info(f"Имя бота найдено точным совпадением: {name} | Чат: {message.chat.title if hasattr(message.chat, 'title') else 'Private'} | Пользователь: {message.from_user.first_name if message.from_user else 'Unknown'}")
            return True
    
    # Then check for fuzzy matches
    text_words = re.sub(r'[^\w\s]', '', text_to_check).lower().split()
    for word in text_words:
        for name in bot_names:
            # For @ mentions, strip the @ for comparison
            name_stripped = name[1:] if name.startswith('@') else name
            similarity = SequenceMatcher(None, name_stripped, word).ratio()
            if similarity > name_match_threshold:
                logger.info(f"Имя бота найдено по проценту сходства: {name} | Процент сходства: {similarity * 100:.2f}% | Чат: {message.chat.title if hasattr(message.chat, 'title') else 'Private'} | Пользователь: {message.from_user.first_name if message.from_user else 'Unknown'}")
                return True
    
    return False

# Function to check if message is a reply to the bot
def is_reply_to_bot(message):
    if hasattr(message, 'reply_to_message') and message.reply_to_message:
        if message.reply_to_message.from_user and message.reply_to_message.from_user.is_self:
            logger.info(f"Обнаружен реплай на сообщение бота | Чат: {message.chat.title if message.chat else 'Unknown'} | Пользователь: {message.from_user.first_name if message.from_user else 'Unknown'}")
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
                is_reply_to_bot(message) or 
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
                        group_messages = message_groups[chat_id]['messages']
                        last_client, last_message = group_messages[-1]
                        
                        content_type = "text"
                        if last_message.photo:
                            content_type = "photo"
                        elif last_message.sticker:
                            content_type = "sticker"
                        elif last_message.animation:
                            content_type = "GIF"
                        
                        content = last_message.text or last_message.caption or (last_message.sticker.emoji if last_message.sticker else (extract_gif_info(last_message.animation) if last_message.animation else "unknown"))
                        chat_title = last_message.chat.title or "Unknown Chat"
                        user_first_name = last_message.from_user.first_name if last_message.from_user and last_message.from_user.first_name else "Unknown"
                        user_last_name = last_message.from_user.last_name if last_message.from_user and last_message.from_user.last_name else ""
                        user_username = message.from_user.username if message.from_user and message.from_user.username else "Unknown"
                        
                        # Log reply information if available
                        reply_info = ""
                        if hasattr(last_message, 'reply_to_message') and last_message.reply_to_message:
                            reply_user = last_message.reply_to_message.from_user
                            reply_name = f"{reply_user.first_name} {reply_user.last_name or ''}".strip() if reply_user else "Unknown"
                            is_bot_reply = reply_user and reply_user.is_self
                            reply_info = f" | Reply to: {reply_name} ({'bot' if is_bot_reply else 'user'})"
                        
                        logger.info(f"Обработка группы сообщений ({len(group_messages)} сообщений). Последнее сообщение: {content_type}: {content} | Чат: {chat_title} | Пользователь: {user_username}{reply_info}")
                        
                        # Create a message_id to index mapping for quick lookups
                        message_id_map = {str(msg[1].id): idx for idx, msg in enumerate(group_messages)}
                        
                        # Build a reply chain map for context
                        reply_chain_map = {}
                        for _, msg in group_messages:
                            if hasattr(msg, 'reply_to_message') and msg.reply_to_message:
                                reply_chain_map[str(msg.id)] = str(msg.reply_to_message.id)
                            elif hasattr(msg, 'reply_to_message_id') and msg.reply_to_message_id:
                                reply_chain_map[str(msg.id)] = str(msg.reply_to_message_id)
                        
                        # Analyze all images in the message group
                        for _, group_message in group_messages:
                            if hasattr(group_message, 'photo') and group_message.photo:
                                try:
                                    image_description = await analyze_image(group_message.photo)
                                    msg_content = group_message.text or group_message.caption or ""
                                    if msg_content:
                                        group_message._text = f"[Image: {image_description}] {msg_content}"
                                        group_message._caption = f"[Image: {image_description}] {msg_content}"
                                    else:
                                        group_message._text = f"[Image: {image_description}]"
                                        group_message._caption = f"[Image: {image_description}]"
                                except Exception as e:
                                    logger.error(f"Error processing image in group: {e}")
                        
                        response_data = await get_response(
                            message=last_message,
                            chat_id=chat_id,
                            message_id=last_message.id,
                            name=f"{user_first_name} {user_last_name}".strip(),
                            group_messages=group_messages
                        )
                        
                        logger.info(f"Response data: {response_data}")
                        
                        try:
                            if "messages" in response_data:
                                for idx, msg_data in response_data["messages"].items():
                                    msg_type = msg_data.get("type", "text")
                                    msg_content = msg_data.get("content", "")
                                    msg_target = msg_data.get("target", None)
                                    
                                    # Default to replying to the last message
                                    target_client, target_message = last_client, last_message
                                    
                                    # If a target is specified and exists in our current message group, use that instead
                                    if str(msg_target).isdigit() and msg_target in message_id_map:
                                        target_idx = message_id_map[msg_target]
                                        target_client, target_message = group_messages[target_idx]
                                        msg_target = int(msg_target)
                                        logger.info(f"Targeting specific message id {msg_target} in current group")
                                    else:
                                        msg_target = None
                                    
                                    if msg_type == "text" and msg_content:
                                        await simulate_typing(target_client, chat_id, msg_content)
                                        await target_client.send_message(chat_id=chat_id, text=msg_content, reply_to_message_id=msg_target)
                                    elif msg_type == "gif" and msg_content:
                                        await send_gif(target_client, chat_id, msg_content)
                                    elif msg_type == "sticker" and msg_content:
                                        await send_random_sticker(target_client, chat_id, msg_content)
                            else:
                                logger.error("Invalid response format from Mistral")
                        except Exception as e:
                            logger.error(f"Error processing response: {e}")
                            await last_message.reply("Произошла ошибка при обработке ответа.")
                        
                        del message_groups[chat_id]
                
                timer = asyncio.create_task(process_message_group(chat_id))
                message_groups[chat_id]['timer'] = timer
            else: 
                # Log reply information even for ignored messages
                reply_info = ""
                if hasattr(message, 'reply_to_message') and message.reply_to_message:
                    reply_user = message.reply_to_message.from_user
                    reply_name = f"{reply_user.first_name} {reply_user.last_name or ''}".strip() if reply_user else "Unknown"
                    is_bot_reply = reply_user and reply_user.is_self
                    reply_info = f" | Reply to: {reply_name} ({'bot' if is_bot_reply else 'user'})"
                
                logger.info(f"Сообщение проигнорировано: {message.text or message.caption or 'Не текстовое сообщение'} | Чат: {(message.chat.title if message.chat else 'Unknown Chat')} | Пользователь: {(message.from_user.username if message.from_user else 'Unknown')}{reply_info}")
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
