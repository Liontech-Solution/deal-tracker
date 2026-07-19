import { Module } from '@nestjs/common';

import { AuthModule } from '../auth/auth.module';
import { InterestsController } from './interests.controller';
import { InterestsService } from './interests.service';

@Module({
  imports: [AuthModule],
  controllers: [InterestsController],
  providers: [InterestsService],
})
export class InterestsModule {}
