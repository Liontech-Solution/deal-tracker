import { Module } from '@nestjs/common';

import { TelegramModule } from '../telegram/telegram.module';
import { MatchingService } from './matching.service';

/**
 * Job de matching de ofertas. Sin controladores: se invoca desde el CLI
 * `src/jobs/matching.job.ts` (CronJob de k3s tras el scraper), no por HTTP.
 */
@Module({
  imports: [TelegramModule],
  providers: [MatchingService],
  exports: [MatchingService],
})
export class MatchingModule {}
