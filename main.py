import json
import time
import random
import asyncio
import logging
import re
import leo
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

def chat_filter_func(_, __, message):
    if message.from_user and message.from_user.username == "leomatchbot":
        return False
    if message.text and message.text.strip().lower() in ['/leo_start', '/leo_stop']:
        return False
    if config['allowed_chats'] and message.chat.id in config['allowed_chats']:
        return True
    return filters.private and (filters.text | filters.sticker | filters.animation)

async def get_chat_history(chat_id, limit, current_message_id):
    messages = []
    current_role = None
    current_content = []
    
    async for message in app.get_chat_history(chat_id, limit=limit, offset_id=current_message_id):
        if message.text or message.sticker or message.animation:
            name = f"{message.from_user.first_name} {message.from_user.last_name or ''}"
            role = "assistant" if message.from_user.is_self else "user"
            mentioned = is_mentioned(message)
            
            if role != current_role:
                if current_role:
                    messages.append({"role": current_role, "content": "\n".join(current_content)})
                current_role = role
                current_content = []
            
            message_text = f"[{name.strip()}]: {'[Mentioned] ' if mentioned else ''}"
            if message.text:
                message_text += message.text
            elif message.sticker:
                message_text += '{'+message.sticker.emoji+' sticker}'
            elif message.animation:
                gif_info = extract_gif_info(message.animation)
                message_text += '{'+gif_info+' gif}'
            
            current_content.append(message_text)
    if current_role:
        messages.append({"role": current_role, "content": "\n".join(current_content[::-1])})
    logger.info(messages[::-1])
    return messages[::-1]

def extract_gif_info(animation):
    if animation.file_name:
        return animation.file_name.split('.')[0]
    elif animation.file_unique_id:
        return animation.file_unique_id
    else:
        return "Unknown GIF"

async def get_response(message, chat_id, message_id, name="unknown"):
    chat_history = await get_chat_history(chat_id, config['message_memory'], message_id)
    
    if isinstance(message, str):
        content = message
    elif message.text:
        content = message.text
    elif message.sticker:
        content = '{'+message.sticker.emoji+' sticker}'
    elif message.animation:
        gif_info = extract_gif_info(message.animation)
        content = '{'+gif_info+' gif}'
    else:
        content = "Unsupported message type"
    
    chat_history.append({"role": "user", "content": f"[{name}]: {content}"})
    
    chat_response = client.agents.complete(agent_id=config['mistral_agent_id'], messages=chat_history)
    assistant_response = chat_response.choices[0].message.content
    return assistant_response

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
    bot_names = config['bot_names']
    name_match_threshold = config['name_match_threshold']
    text = re.sub(r'[^\w\s]', '', message.text or '').lower().split()
    for word in text:
        for name in bot_names:
            if SequenceMatcher(None, name, word).ratio() > name_match_threshold:
                logger.info(f"Имя бота найдено по проценту сходства: {name} | Процент сходства: {SequenceMatcher(None, name, word).ratio() * 100:.2f}% | Чат: {message.chat.title} | Пользователь: {message.from_user.first_name}")
                return True
    return False

async def send_gif(client, chat_id, query):
    try:
        results = await client.get_inline_bot_results("gif", query)
        if results.results:
            await client.send_inline_bot_result(chat_id, results.query_id, results.results[0].id)
            return True
    except Exception as e:
        logger.error(f"Ошибка при отправке GIF: {e}")
    return False

async def send_random_sticker(client, chat_id, emoji):
    try:
        matching_stickers = []
        for sticker_set_name in config['sticker_sets']:
            try:
                sticker_set = await client.invoke(functions.messages.GetStickerSet(
                    stickerset=types.InputStickerSetShortName(short_name=sticker_set_name),
                    hash=0
                ))
                
                for document in sticker_set.documents:
                    for attribute in document.attributes:
                        if isinstance(attribute, types.DocumentAttributeSticker):
                            if attribute.alt == emoji:
                                matching_stickers.append(document)
                                break

            except Exception as e:
                logger.error(f"Ошибка при получении набора стикеров {sticker_set_name}: {e}")

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

@app.on_message(filters.create(chat_filter_func))
async def auto_reply(client, message):
    await message_queue.put([client, message])

async def process_queue():
    global is_online, last_activity_time
    while True:
        try:
            client, message = await message_queue.get()
            content_type = "text" if message.text else "sticker" if message.sticker else "GIF" if message.animation else "unknown"
            content = message.text or (message.sticker.emoji if message.sticker else (extract_gif_info(message.animation) if message.animation else "unknown"))
            logger.info(f"Обработка сообщения: {content_type}: {content} | Чат: {message.chat.title} | Пользователь: {message.from_user.username}")
            
            if (message.reply_to_message and message.reply_to_message.from_user.is_self) or message.chat.type == ChatType.PRIVATE or is_mentioned(message):
                if not is_online:
                    await asyncio.sleep(random.uniform(config['delay_before_online'][0], config['delay_before_online'][1]))
                    await app.invoke(functions.account.UpdateStatus(offline=False))
                    is_online = True
                    logger.info("Статус: онлайн")
                last_activity_time = time.time()
                await client.read_chat_history(message.chat.id)
                response = await get_response(message=message, chat_id=message.chat.id, message_id=message.id, name=f"{message.from_user.first_name} {message.from_user.last_name}")
                for part in filter(None, response.split(f"[{me.first_name} {me.last_name}]: ")):
                    logger.info(f"Ответ отправлен: {part} | Чат: {message.chat.title} | Пользователь: {message.from_user.username}")
                    await simulate_typing(client, message.chat.id, part)
                    
                    gif_match = re.search(r'\{(.*?) gif\}', part)
                    sticker_match = re.search(r'\{(.*?) sticker\}', part)
                    
                    if gif_match:
                        gif_query = gif_match.group(1)
                        gif_sent = await send_gif(client, message.chat.id, gif_query)
                        if gif_sent:
                            part = re.sub(r'\{.*? gif\}', '', part).strip()
                    elif sticker_match:
                        sticker_emoji = sticker_match.group(1)
                        sticker_sent = await send_random_sticker(client, message.chat.id, sticker_emoji)
                        if sticker_sent:
                            part = re.sub(r'\{.*? sticker\}', '', part).strip()
                    
                    if part:
                        await message.reply(part)
            else:
                logger.info(f"Сообщение проигнорировано: {content_type}: {content} | Чат: {message.chat.title} | Пользователь: {message.from_user.username}")
        except Exception as e:
            logger.error(f"Ошибка при обработке сообщения: {e}")
        finally:
            message_queue.task_done()

async def main():
    global me
    await app.start()
    me = await app.get_me()
    await app.invoke(functions.account.UpdateStatus(offline=True))
    asyncio.create_task(process_queue())
    leo.setup(app, client, config)
    await simulate_online_status()

if __name__ == "__main__":
    app.run(main())
