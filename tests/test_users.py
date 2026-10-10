from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
import discord

import bot
from database import Database
from integrations import MockIntegration
from mock_data import build_mock_data
from models import Campus
from sync_engine import SyncEngine
from user_data import UserData
from user_reminders import UserReminderService


NOW=datetime(2026,10,10,7,tzinfo=timezone.utc)


class UserTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary=tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.database=Database(Path(temporary.name)/'users.db')
        self.database.initialize()
        data=build_mock_data(NOW)
        data=replace(data,assignments=tuple(replace(a,due_at=NOW+timedelta(hours=2)) for a in data.assignments))
        await SyncEngine(self.database,{'mock':MockIntegration(data)}).sync_all()
        self.users=UserData(self.database)
        self.users.setup(101,Campus.Mumbai,['CITS1401'],reminders=True,thresholds=(3,))
        self.users.setup(202,Campus.Chennai,['STAT1400'],timezone_name='UTC',reminders=False)

    async def test_profiles_preferences_and_course_sets(self):
        self.assertEqual(self.users.profile(101)['campus'],'Mumbai')
        self.assertEqual(self.users.profile(202)['timezone'],'UTC')
        self.assertEqual(self.users.profile(101)['remind_168'],0)
        self.assertEqual([c.code for c in self.users.courses(101)],['CITS1401'])
        self.assertEqual([c.code for c in self.users.courses(202)],['STAT1400'])
        self.assertIsNone(self.users.profile(303))

    async def test_filter_isolation_and_no_external_data(self):
        records=self.database.get_upcoming_assignments(NOW)
        first=self.users.filter(101,records)
        second=self.users.filter(202,records)
        self.assertTrue(all(a.course_code=='CITS1401' and a.campus==Campus.Mumbai for a in first))
        self.assertTrue(all(a.course_code=='STAT1400' and a.campus==Campus.Chennai for a in second))
        self.assertFalse({a.id for a in first}&{a.id for a in second})
        self.assertEqual(self.users.filter(101,[replace(first[0],source='blackboard')]),[])
        self.assertEqual(self.users.filter(303,records),[])

    async def test_commands_use_invoking_user_and_cannot_widen_campus(self):
        async with bot.UwaBot(self.database) as client:
            for uid,campus,code in [(101,Campus.Mumbai,'CITS1401'),(202,Campus.Chennai,'STAT1400')]:
                for command in ['today','tomorrow','week','due','nextclass','announcements','course']:
                    interaction=SimpleNamespace(user=SimpleNamespace(id=uid),response=SimpleNamespace(send_message=AsyncMock()))
                    arguments={'course_code':code} if command=='course' else {}
                    with patch('academic_commands.utc_now',return_value=NOW):
                        await client.tree.get_command(command).callback(interaction,**arguments)
                    payload=interaction.response.send_message.call_args.kwargs
                    self.assertTrue(payload['ephemeral'])
                    self.assertIn(campus.value,payload['embed'].description)
                    values=' '.join(f.name+' '+f.value for f in payload['embed'].fields)
                    self.assertNotIn('BUSN1100',values)
                interaction.response.send_message.reset_mock()
                with patch('academic_commands.utc_now',return_value=NOW):
                    await client.tree.get_command('due').callback(interaction,campus=Campus.Chennai if campus==Campus.Mumbai else Campus.Mumbai)
                self.assertTrue(interaction.response.send_message.call_args.kwargs['embed'].fields[0].value.startswith('No '))

    async def test_setup_profile_and_timezone(self):
        async with bot.UwaBot(self.database) as client:
            interaction=SimpleNamespace(user=SimpleNamespace(id=303),response=SimpleNamespace(send_message=AsyncMock()))
            await client.tree.get_command('today').callback(interaction)
            self.assertIn('/setup',interaction.response.send_message.call_args.kwargs['embed'].description)
            await client.tree.get_command('setup').callback(interaction,Campus.Chennai,'CITS1401,STAT1400')
            self.assertEqual(len(self.users.courses(303)),2)
            await client.tree.get_command('profile').callback(interaction)
            self.assertIn('Chennai',interaction.response.send_message.call_args.kwargs['embed'].description)
            interaction.user.id=202
            with patch('academic_commands.utc_now',return_value=NOW):
                await client.tree.get_command('due').callback(interaction)
            embed=interaction.response.send_message.call_args.kwargs['embed']
            self.assertIn('UTC',embed.footer.text)
            self.assertTrue(all(' UTC' in f.value for f in embed.fields))

    async def test_invalid_setup_is_atomic_and_no_secrets_schema(self):
        with self.assertRaises(ValueError):
            self.users.setup(101,Campus.Chennai,['UNKNOWN'])
        self.assertEqual(self.users.profile(101)['campus'],'Mumbai')
        with self.database.connect() as connection:
            columns={r['name'] for r in connection.execute('PRAGMA table_info(integration_accounts)')}
            self.assertFalse(columns & {'password','token','access_token','refresh_token','secret'})
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM integration_accounts').fetchone()[0],0)

    async def test_reminder_delivery_is_private_and_preference_scoped(self):
        deliver=AsyncMock()
        service=UserReminderService(self.users,deliver,lambda:NOW)
        await service.check()
        self.assertTrue(deliver.await_count>0)
        self.assertTrue(all(call.args[0]==101 and call.args[1].course_code=='CITS1401' for call in deliver.await_args_list))
        count=deliver.await_count
        await service.check()
        self.assertEqual(deliver.await_count,count)
        self.users.setup(202,Campus.Chennai,['STAT1400'],reminders=True,thresholds=(24,))
        await service.check()
        self.assertEqual(deliver.await_count,count)
        self.users.setup(202,Campus.Chennai,['STAT1400'],reminders=True,thresholds=(3,))
        await service.check()
        self.assertTrue(any(c.args[0]==202 for c in deliver.await_args_list))

    async def test_failed_delivery_does_not_mark_sent_or_block_other_user(self):
        self.users.setup(202,Campus.Chennai,['STAT1400'],reminders=True,thresholds=(3,))
        async def deliver(uid,assignment):
            if uid==101:
                raise discord.Forbidden(SimpleNamespace(status=403,reason='Forbidden'),'DM disabled')
        await UserReminderService(self.users,deliver,lambda:NOW).check()
        with self.database.connect() as connection:
            rows=connection.execute('SELECT * FROM user_sent_reminders').fetchall()
        self.assertTrue(rows)
        self.assertTrue(all(r['user_id']=='202' and r['sent_at'] for r in rows))

    async def test_reopen_migration_preserves_records_and_user_reminders(self):
        await UserReminderService(self.users,AsyncMock(),lambda:NOW).check()
        self.database.initialize()
        self.database.initialize()
        reopened=UserData(Database(self.database.path))
        self.assertEqual(reopened.profile(101)['campus'],'Mumbai')
        self.assertEqual(len(reopened.courses(202)),1)
        deliver=AsyncMock()
        await UserReminderService(reopened,deliver,lambda:NOW).check()
        deliver.assert_not_awaited()
        self.assertEqual(len(self.database.get_course('CITS1401')),2)

    async def test_single_user_migration_does_not_guess_record_ownership(self):
        assignment=self.database.get_upcoming_assignments(NOW)[0]
        self.database.claim_reminder(assignment,3,NOW)
        self.database.mark_reminder_sent(assignment,3,NOW)
        with self.database.connect() as connection:
            for table in ['user_sent_reminders','integration_accounts','user_courses','user_preferences','users']:
                connection.execute(f'DROP TABLE {table}')
        self.database.initialize()
        self.assertIsNone(self.users.profile(101))
        with self.database.connect() as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM sent_reminders').fetchone()[0],1)
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM user_sent_reminders').fetchone()[0],0)
            self.assertEqual(connection.execute('PRAGMA foreign_key_check').fetchall(),[])
        self.assertEqual(len(self.database.get_upcoming_assignments(NOW)),8)

    async def test_bot_delivers_only_to_target_dm(self):
        async with bot.UwaBot(self.database) as client:
            target=SimpleNamespace(send=AsyncMock())
            with patch.object(client,'get_user',return_value=target) as lookup:
                await client.deliver_user_reminder(202,self.users.filter(202,self.database.get_upcoming_assignments(NOW))[0])
            lookup.assert_called_once_with(202)
            target.send.assert_awaited_once()
            self.assertIn('UTC',target.send.call_args.kwargs['embed'].fields[0].value)
