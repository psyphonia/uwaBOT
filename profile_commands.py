import asyncio
from enum import Enum
import discord
from models import Campus
from user_data import UserData


class TimezoneChoice(str, Enum):
    India='Asia/Kolkata'
    UTC='UTC'


def register_profile_commands(tree, users: UserData) -> None:
    @tree.command(name='setup',description='Choose your campus, sample courses and reminder preferences.')
    async def setup(interaction: discord.Interaction, campus: Campus, courses: str = '',
                    timezone: TimezoneChoice = TimezoneChoice.India, reminders: bool = False,
                    seven_days: bool = True, one_day: bool = True, three_hours: bool = True) -> None:
        try:
            await asyncio.to_thread(users.setup,interaction.user.id,campus,courses.split(','),timezone.value,
                                    reminders,tuple(t for t, enabled in [(168,seven_days),(24,one_day),(3,three_hours)] if enabled))
        except ValueError as error:
            await interaction.response.send_message(str(error),ephemeral=True)
            return
        await interaction.response.send_message('Profile saved. Reminders use private Discord messages. University linking is disabled.',ephemeral=True)
        if reminders and not tree.client.reminder_loop.is_running():
            tree.client.reminder_loop.start()

    @tree.command(name='profile',description='Show your campus, linked sample courses and preferences.')
    async def profile(interaction: discord.Interaction) -> None:
        record=await asyncio.to_thread(users.profile,interaction.user.id)
        if not record:
            await interaction.response.send_message('Run /setup to choose your campus and sample course codes.',ephemeral=True)
            return
        courses=await asyncio.to_thread(users.courses,interaction.user.id)
        embed=discord.Embed(title='Your profile',description=f"{record['campus']} • {record['timezone']}")
        embed.add_field(name='Sample courses',value=', '.join(c.code for c in courses)[:1024] or 'None selected. Use /setup with comma-separated course codes.')
        embed.add_field(name='Reminders',value='Private messages enabled' if record['reminders_enabled'] else 'Disabled')
        embed.set_footer(text='University account linking disabled • No university credentials stored')
        await interaction.response.send_message(embed=embed,ephemeral=True)
