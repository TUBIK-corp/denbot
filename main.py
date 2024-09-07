import json, time, random, asyncio, logging, re
from difflib import SequenceMatcher
from mistralai import Mistral
from pyrogram import Client, filters
from pyrogram.enums import ChatType, ChatAction
from pyrogram.raw import functions

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

with open('config.json', 'r', encoding='utf-8') as f:
    config = json.load(f)

messages = [{}]

client = Mistral(api_key=config['mistral_api_key'])
app = Client("my_account", api_id=config['tg_api_id'], api_hash=config['tg_api_hash'])

last_activity_time = 0
is_online = False
message_queue = asyncio.Queue()

def chat_filter_func(_, __, message):
    if config['allowed_chats'] and message.chat.id in config['allowed_chats']:
        return True
    return filters.private and filters.text

def get_response(message, name="unknown"):
    global messages
    messages.append({"role": "user", "content": f"[{name}]: {message}"})
    if len(messages) > config['message_memory']: messages = messages[-config['message_memory']:]
    chat_response = client.agents.complete(agent_id=config['mistral_agent_id'], messages=messages)
    assistant_response = chat_response.choices[0].message.content
    messages.append({"role": "assistant", "content": assistant_response})
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
    text = re.sub(r'[^\w\s]', '', message.text).lower().split()
    for word in text:
        for name in bot_names:
            if SequenceMatcher(None, name, word).ratio() > name_match_threshold:
                logger.info(f"Имя бота найдено по проценту сходства: {name} | Процент сходства: {SequenceMatcher(None, name, word).ratio() * 100:.2f}% | Чат: {message.chat.title} | Пользователь: {message.from_user.first_name}")
                return True
    return False

@app.on_message(filters.create(chat_filter_func))
async def auto_reply(client, message):
    await message_queue.put([client, message])

async def process_queue():
    global is_online, last_activity_time, messages
    while True:
        client, message = await message_queue.get()
        logger.info(f"Обработка сообщения: {message.text} | Чат: {message.chat.title} | Пользователь: {message.from_user.first_name}")
        if (message.reply_to_message and message.reply_to_message.from_user.is_self) or message.chat.type == ChatType.PRIVATE or is_mentioned(message):
            if not is_online:
                await asyncio.sleep(random.uniform(config['delay_before_online'][0], config['delay_before_online'][1]))
                await app.invoke(functions.account.UpdateStatus(offline=False))
                is_online = True
                logger.info("Статус: онлайн")
            last_activity_time = time.time()
            await client.read_chat_history(message.chat.id)
            response = get_response(message=message.text, name=message.from_user.first_name)
            logger.info(f"Ответ отправлен: {response} | Чат: {message.chat.title} | Пользователь: {message.from_user.first_name}")
            await simulate_typing(client, message.chat.id, response)
            await message.reply(response)
        else:
            messages.append({"role": "user", "content": f"[{message.from_user.first_name}]: {message}"})
            logger.info(f"Сообщение проигнорировано: {message.text} | Чат: {message.chat.title} | Пользователь: {message.from_user.first_name}")
        message_queue.task_done()

async def main():
    await app.start()
    await app.invoke(functions.account.UpdateStatus(offline=True))
    asyncio.create_task(process_queue())
    await simulate_online_status()

if __name__ == "__main__":
    app.run(main())
