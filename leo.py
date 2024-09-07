import asyncio, re
from pyrogram import Client, filters
from pyrogram.types import Message
from mistralai import Mistral

LEO_BOT_USERNAME = "leomatchbot"

def clean_response(response):
    pattern = rf'^\[{"Пупс"}\]:\s*'
    response = re.sub(pattern, '', response, flags=re.IGNORECASE)
    return response.strip()

class LeoBot:
    def __init__(self, app: Client, mistral_client: Mistral, config: dict):
        self.app = app
        self.mistral_client = mistral_client
        self.config = config
        self.is_running = False
        self.leo_chat_id = None

    async def start_bot(self):
        self.is_running = True
        self.leo_chat_id = await self.get_chat_id(LEO_BOT_USERNAME)
        await self.initial_setup()
        await self.main_loop()

    async def stop_bot(self):
        self.is_running = False

    async def get_chat_id(self, username: str) -> int:
        chat = await self.app.get_chat(username)
        return chat.id

    async def send_message(self, text: str):
        await self.app.send_message(self.leo_chat_id, text)
        await asyncio.sleep(1)

    async def get_last_message(self) -> Message:
        async for message in self.app.get_chat_history(self.leo_chat_id, limit=1):
            return message

    async def initial_setup(self):
        await self.send_message("/start")
        await self.send_message("1")

    async def rate_profile(self, profile_text: str) -> int:
        response = self.mistral_client.agents.complete(
            agent_id="ag:93cb32c3:20240907:leo:ae61fce4",
            messages=[
                {"role": "user", "content": f"{profile_text}"}
            ]
        )
        print(response.choices[0].message.content.strip())
        rating = int(response.choices[0].message.content.strip()[0])
        return rating

    async def get_reaction(self, rating: int) -> str:
        if rating <= 5:
            return "👎"
        elif rating <= 7:
            return "❤️"
        else:
            return "💌 / 📹"

    async def main_loop(self):
        while self.is_running:
            try:
                profile_message = await self.get_last_message()
                rating = await self.rate_profile(profile_message.text)
                reaction = await self.get_reaction(rating)
                await self.send_message(reaction)
                if reaction == "💌 / 📹":
                    response = self.mistral_client.agents.complete(agent_id=self.config['mistral_agent_id'], messages=[{"role": "user", "content": f"Ты листал бота для поиска знакомств и тебе очень понравилась эта анкета: {profile_message.text}, придумай что написать ей, пиши влюбчиво и очень возбуждённо, но веди себя максимально серьёзно и умно!"}])
                    await self.send_message(clean_response(response.choices[0].message.content.strip()))

            except Exception as e:
                print(f"An error occurred: {e}")
            await asyncio.sleep(5)

def setup(app: Client, mistral_client: Mistral, config: dict):
    leo_bot = LeoBot(app, mistral_client, config)

    @app.on_message(filters.command("leo_start") & filters.private)
    async def start_leo_bot(client, message):
        await message.reply("Запускаю Leo бота...")
        await leo_bot.start_bot()

    @app.on_message(filters.command("leo_stop") & filters.private)
    async def stop_leo_bot(client, message):
        await leo_bot.stop_bot()
        await message.reply("Leo бот остановлен.")
