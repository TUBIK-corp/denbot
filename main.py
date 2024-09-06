import json, time, random, asyncio, logging
from mistralai import Mistral
from pyrogram import Client, filters
from pyrogram.enums import ChatType, ChatAction
from pyrogram.raw import functions

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

with open('config.json', 'r', encoding='utf-8') as f:
    config = json.load(f)

with open('messages.json', 'r', encoding='utf-8') as f:
    messages = json.load(f)

client = Mistral(api_key=config['mistral_api_key'])
app = Client("my_account", api_id=config['tg_api_id'], api_hash=config['tg_api_hash'])

last_activity_time = 0
is_online = False
message_queue = asyncio.Queue()

def allowed_chat(_, __, message):
    return message.chat.id in config['allowed_chats']

def get_response(message, name="unknown", ping=False):
    global messages
    messages.append({"role": "user", "content": f"[user: {name}, ping: {ping}]: {message}"})
    if len(messages) > config['message_memory']: messages = messages[-config['message_memory']:]

    chat_response = client.agents.complete(agent_id=config['mistral_agent_id'], messages=messages)

    assistant_response = chat_response.choices[0].message.content
    messages.append({"role": "assistant", "content": assistant_response})
    
    with open('messages.json', 'w', encoding='utf-8') as file:
        json.dump(messages, file, indent=2)
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

@app.on_message(filters.text & (filters.create(allowed_chat) | filters.private))
async def auto_reply(client, message):
    await message_queue.put([client, message])
    logger.info(f"Добавлено в очередь: {message.text} | Чат: {message.chat.title} | Пользователь: {message.from_user.first_name}")
    
async def process_queue():
    global is_online, last_activity_time
    while True:
        client, message = await message_queue.get()
        logger.info(f"Обработка сообщения: {message.text} | Чат: {message.chat.title} | Пользователь: {message.from_user.first_name}")
        if not is_online:
            await asyncio.sleep(random.uniform(config['delay_before_online'][0], config['delay_before_online'][1]))
            await app.invoke(functions.account.UpdateStatus(offline=False))
            is_online = True
            logger.info("Статус: онлайн")
        last_activity_time = time.time()
        await client.read_chat_history(message.chat.id)
        response = get_response(message=message.text, name=message.from_user.username, ping = (message.reply_to_message and message.reply_to_message.from_user.is_self) or message.chat.type == ChatType.PRIVATE)
        logger.info(f"Ответ отправлен: {response} | Чат: {message.chat.title} | Пользователь: {message.from_user.first_name}")
        if response != "[mute]":
            await simulate_typing(client, message.chat.id, response)
            await message.reply(response)
        message_queue.task_done()

async def main():
    await app.start()
    await app.invoke(functions.account.UpdateStatus(offline=True))
    asyncio.create_task(process_queue())
    await simulate_online_status()

if __name__ == "__main__":
    app.run(main())
