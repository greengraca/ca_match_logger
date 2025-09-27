import discord
MOD_ROLE_NAMES = {"MODERATOR", "ADMIN", "ADMINISTRATOR", "MOD"}
def is_mod(member: discord.Member) -> bool:
    if member.guild_permissions.administrator or member.guild_permissions.manage_messages:
        return True
    return any((r.name or "").upper() in MOD_ROLE_NAMES for r in member.roles)
