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


async def process_message(m, edited: bool, engine: Engine, notifier: Notifier) -> None:
    """Traite un message du canal. Quoi qu'il arrive, l'utilisateur est prévenu si ça échoue."""
    text = m.message or ""
    photo = None
    try:
        if m.photo:
            try:
                photo = await asyncio.wait_for(m.download_media(file=bytes), 30)
            except Exception as e:
                log.exception("Image du message %s non téléchargée", m.id)
                await notifier.send(f"❗ Image non téléchargée ({type(e).__name__}), analyse sur le texte seul.\n"
                                    f"Regarde le canal toi-même. Texte : {text[:300] or '(aucun)'}")
        await engine.on_channel_message(Incoming(
            id=m.id, date=m.date, text=text, photo=photo,
            edited=edited, forwarded=m.fwd_from is not None))
    except Exception as e:
        log.exception("Message %s non traité", m.id)
        await notifier.send(f"❗ Message non traité ({type(e).__name__}) : regarde le canal toi-même.\n"
                            f"{text[:400] or '(image sans texte)'}")


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
    await client.catch_up()  # récupère ce qui a été manqué pendant une coupure

    queue: asyncio.Queue = asyncio.Queue()
    seen: set[int] = {m.id for m in await client.get_messages(channel, limit=30)}  # l'historique n'est pas rejoué

    def enqueue(m, edited: bool = False, source: str = "live") -> None:
        if not edited:
            if m.id in seen:
                return
            seen.add(m.id)
            if source == "poll":
                log.warning("Message %s récupéré par le polling : Telegram ne l'avait pas poussé", m.id)
            if len(seen) > 500:
                seen.difference_update(sorted(seen)[:250])
        queue.put_nowait((m, edited))

    @client.on(events.NewMessage(chats=channel))
    async def on_new(event):
        enqueue(event.message)

    @client.on(events.MessageEdited(chats=channel))
    async def on_edit(event):
        enqueue(event.message, edited=True)

    @client.on(events.NewMessage(chats="me", pattern=r"^/"))
    async def on_cmd(event):
        await event.reply(await engine.on_command(event.raw_text))

    async def worker():
        # Un seul worker : les messages sont traités dans l'ordre (le SL arrive souvent après le signal).
        while True:
            m, edited = await queue.get()
            await process_message(m, edited, engine, notifier)

    async def poller():
        # Filet de sécurité : Telegram peut cesser de pousser les mises à jour d'un canal sans erreur.
        while True:
            await asyncio.sleep(cfg.poll_interval_s)
            try:
                for m in reversed(await client.get_messages(channel, limit=10)):
                    enqueue(m, source="poll")
            except Exception as e:
                log.warning("Polling Telegram échoué : %s", e)

    asyncio.create_task(worker())
    asyncio.create_task(poller())
    if executor:
        asyncio.create_task(engine.monitor_loop())
    await notifier.send(f"🤖 Sniper démarré (mode {cfg.mode}) sur « {getattr(channel, 'title', cfg.tg_channel)} ».")
    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
