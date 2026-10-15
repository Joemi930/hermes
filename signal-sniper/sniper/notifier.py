"""Notifications : WhatsApp (CallMeBot), tes « Messages enregistrés » Telegram, et un webhook optionnel (Hermès…)."""
import asyncio
import logging

import httpx

log = logging.getLogger(__name__)


class Notifier:
    def __init__(self, cfg):
        self.cfg = cfg
        self.tg = None
        self.http = httpx.AsyncClient(timeout=15)

    def attach_telegram(self, client):
        self.tg = client

    async def send(self, text: str) -> None:
        log.info("NOTIF: %s", text.replace("\n", " | "))
        jobs = []
        if self.tg and self.cfg.notify_telegram_saved:
            jobs.append(self.tg.send_message("me", text))
        if self.cfg.callmebot_phone and self.cfg.callmebot_apikey:
            jobs.append(self.http.get("https://api.callmebot.com/whatsapp.php", params={
                "phone": self.cfg.callmebot_phone, "text": text, "apikey": self.cfg.callmebot_apikey}))
        if self.cfg.notify_webhook_url:
            jobs.append(self.http.post(self.cfg.notify_webhook_url, json={"text": text}))
        for r in await asyncio.gather(*jobs, return_exceptions=True):
            if isinstance(r, Exception):
                log.warning("Notification échouée : %s", r)
