import discord

class PaginatorView(discord.ui.View):
    def __init__(
        self,
        author: discord.User,
        pages: list[str],
        *,
        title: str = "📜 Game Dump",
        timeout: float = 60,
    ):
        super().__init__(timeout=timeout)
        self.author = author
        self.pages = pages
        self.current = 0
        self.title = title

    async def _send(self, interaction: discord.Interaction):
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("You can't interact with this paginator.", ephemeral=True)
            return
        embed = discord.Embed(
            title=f"{self.title} (Page {self.current + 1}/{len(self.pages)})",
            description=self.pages[self.current],
        )
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary)
    async def prev(self, _, interaction: discord.Interaction):
        if self.current > 0:
            self.current -= 1
            await self._send(interaction)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary)
    async def next(self, _, interaction: discord.Interaction):
        if self.current < len(self.pages) - 1:
            self.current += 1
            await self._send(interaction)
