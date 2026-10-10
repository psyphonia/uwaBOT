import asyncio
from datetime import datetime
import discord
from reminders import applicable_threshold, utc_now, LOGGER
from user_data import UserData


class UserReminderService:
    def __init__(self, users: UserData, deliver, clock=utc_now):
        self.users, self.deliver, self.clock=users,deliver,clock

    async def check(self) -> None:
        for profile in await asyncio.to_thread(self.users.reminder_users):
            uid=int(profile['user_id'])
            records=await asyncio.to_thread(self.users.database.get_upcoming_assignments,self.clock())
            assignments=await asyncio.to_thread(self.users.filter,uid,records)
            for assignment in assignments:
                threshold=applicable_threshold(assignment.due_at,self.clock())
                if threshold is None or not profile[f'remind_{threshold}']:
                    continue
                if not await asyncio.to_thread(self.users.reminder_state,uid,assignment,threshold,self.clock(),'claim'):
                    continue
                current=await asyncio.to_thread(self.users.profile,uid)
                allowed=await asyncio.to_thread(self.users.filter,uid,[assignment])
                if (not current or not current['reminders_enabled'] or not current[f'remind_{threshold}'] or not allowed
                        or applicable_threshold(assignment.due_at,self.clock())!=threshold):
                    await asyncio.to_thread(self.users.reminder_state,uid,assignment,threshold,self.clock(),'release')
                    continue
                try:
                    await self.deliver(uid,assignment)
                except discord.HTTPException as error:
                    if 400<=error.status<500:
                        await asyncio.to_thread(self.users.reminder_state,uid,assignment,threshold,self.clock(),'release')
                    LOGGER.warning('event=user_reminder_failed')
                    continue
                except Exception:
                    LOGGER.warning('event=user_reminder_uncertain pending_review')
                    continue
                await asyncio.to_thread(self.users.reminder_state,uid,assignment,threshold,self.clock(),'sent')
