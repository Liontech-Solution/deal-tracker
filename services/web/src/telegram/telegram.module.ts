import { Module } from '@nestjs/common';

import { TelegramApiClient } from './telegram-api.client';
import { TelegramLinkService } from './telegram-link.service';
import { TelegramPollingService } from './telegram-polling.service';

/**
 * Bot de Telegram. Sin controladores: la única entrada es el long-polling (apagado por defecto,
 * ver `TelegramPollingService`). Exporta el cliente y el servicio de vínculo para que el futuro
 * job de avisos pueda enviar mensajes y resolver el `chat_id` de cada usuario.
 */
@Module({
  providers: [TelegramApiClient, TelegramLinkService, TelegramPollingService],
  exports: [TelegramApiClient, TelegramLinkService],
})
export class TelegramModule {}
