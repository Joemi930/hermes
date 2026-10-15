"""Lance le sniper : écoute le canal Telegram 24h/24 et exécute les signaux."""
import asyncio
import logging

from telethon import TelegramClient, events

from .config import load_config
from .engine import Engine
from .executor import Executor
from .llm import LLMParser
from .notifier import Notifier
from .parser import Incoming, SignalParser

log = logging.getLogger("sniper")


def channel_ref(value: str):
    v = value.strip()
    return int(v) if v.lstrip("-").isdigit() else v


async def main():
    cfg = load_config()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    executor = None
    if cfg.mode != "notify":
        executor = Executor(cfg)
        await asyncio.to_thread(executor.connect)

    notifier = Notifier(cfg)
    engine = Engine(cfg, executor, notifier, SignalParser(cfg, LLMParser(cfg)))

    client = TelegramClient(cfg.tg_session, cfg.tg_api_id, cfg.tg_api_hash,
                            connection_retries=None, retry_delay=2, auto_reconnect=True)
    await client.connect()
    if not await client.is_user_authorized():
        raise SystemExit("Session Telegram absente : lance d'abord `python -m sniper.login`.")
    notifier.attach_telegram(client)
    channel = await client.get_entity(channel_ref(cfg.tg_channel))
    log.info("Écoute du canal : %s", getattr(channel, "title", channel))

    queue: asyncio.Queue = asyncio.Queue()

    @client.on(events.NewMessage(chats=channel))
    async def on_new(event):
        queue.put_nowait((event.message, False))

    @client.on(events.MessageEdited(chats=channel))
    async def on_edit(event):
        queue.put_nowait((event.message, True))

    @client.on(events.NewMessage(chats="me", pattern=r"^/"))
    async def on_cmd(event):
        await event.reply(await engine.on_command(event.raw_text))

    async def worker():
        # Un seul worker : les messages sont traités dans l'ordre (le SL arrive souvent après le signal).
        while True:
            m, edited = await queue.get()
            try:
                photo = await m.download_media(file=bytes) if m.photo else None
                await engine.on_channel_message(Incoming(
                    id=m.id, date=m.date, text=m.message or "", photo=photo,
                    edited=edited, forwarded=m.fwd_from is not None))
            except Exception:
                log.exception("Message %s non traité", m.id)

    asyncio.create_task(worker())
    if executor:
        asyncio.create_task(engine.monitor_loop())
    await notifier.send(f"🤖 Sniper démarré (mode {cfg.mode}) sur « {getattr(channel, 'title', cfg.tg_channel)} ».")
    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
