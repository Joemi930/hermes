"""À lancer UNE fois (interactif) : connecte ton compte Telegram et liste tes canaux avec leur ID."""
import asyncio

from telethon import TelegramClient

from .config import load_config


async def main():
    cfg = load_config()
    client = TelegramClient(cfg.tg_session, cfg.tg_api_id, cfg.tg_api_hash)
    await client.start()  # demande ton numéro puis le code reçu dans Telegram
    me = await client.get_me()
    print(f"\nConnecté en tant que {me.first_name} (@{me.username}). Session enregistrée.\n")
    print("Tes canaux / groupes (mets l'ID ou le @nom dans TG_CHANNEL) :")
    async for d in client.iter_dialogs():
        if d.is_channel or d.is_group:
            print(f"  {d.id:>16}  {d.name}")
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
