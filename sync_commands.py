import discord
from discord import app_commands
from sync_engine import SyncEngine


def register_sync_command(tree: app_commands.CommandTree, engine: SyncEngine, admin_user_id: int | None = None) -> None:
    @tree.command(name="sync", description="Sync configured academic sources into the local database.")
    @app_commands.default_permissions(administrator=True)
    async def sync(interaction: discord.Interaction) -> None:
        # Runtime enforcement also covers installations where command defaults were overridden.
        if (interaction.guild_id is None or not interaction.permissions.administrator
                or admin_user_id is not None and interaction.user.id != admin_user_id):
            await interaction.response.send_message("Only server administrators can sync data.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            results = await engine.sync_all()
        except Exception:
            await interaction.followup.send("Sync could not complete. Please try again later.", ephemeral=True)
            return
        embed = discord.Embed(title="Academic sync", color=discord.Color.blue())
        for source, result in results.items():
            message = result.error or "\n".join(
                f"{kind}: {counts['inserted']} added, {counts['updated']} updated, {counts['unchanged']} unchanged"
                for kind, counts in result.counts.items())
            embed.add_field(name=source.title(), value=message or "No data.", inline=False)
        if not results:
            embed.description = "No sources configured."
        await interaction.followup.send(embed=embed, ephemeral=True,
                                        allowed_mentions=discord.AllowedMentions.none())
