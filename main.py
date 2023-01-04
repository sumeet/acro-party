import typing

import discord

from game import NUM_ROUNDS, Game, Round

bot = discord.Bot()

GAME_BY_CHANNEL_ID = {}


def get_current_game(ctx):
    return GAME_BY_CHANNEL_ID.get(ctx.channel.id)


def set_current_game(ctx, game):
    GAME_BY_CHANNEL_ID[ctx.channel.id] = game


def remove_current_game(ctx):
    del GAME_BY_CHANNEL_ID[ctx.channel.id]


def make_and_set_new_game(ctx):
    game = Game(ctx.author, ctx.channel)
    set_current_game(ctx, game)
    return game


def mention(user):
    return f"<@{user.id}>"


def game_start_view(game):
    view = discord.ui.View(timeout=None)  # View has an on_timeout which we can use later TODO

    class JoinButton(discord.ui.Button):
        async def callback(self, interaction):
            await interaction.response.defer()
            try:
                await game.add_player(interaction.user)
            except Game.PlayerAlreadyJoinedError:
                await interaction.followup.send(f"You already joined the game!", ephemeral=True)

    view.add_item(JoinButton(label="Join", style=discord.ButtonStyle.primary))

    class StartButton(discord.ui.Button):
        async def callback(self, interaction):
            await interaction.response.defer()
            await game.start()

    view.add_item(StartButton(label="Start", style=discord.ButtonStyle.green))
    return view


def acro_answer_view(game):
    view = discord.ui.View(timeout=None)

    class AnswerModal(discord.ui.Modal):
        async def callback(self, interaction):
            await interaction.response.defer()
            try:
                await game.add_submission(interaction.user, self.children[0].value)
            except Round.SubmissionDoesNotMatchAcroError:
                await interaction.followup.send(
                    "Your answer does not match the acronym! Make sure every word starts with the following letters: "
                    f"**`{game.current_round.acro}`**, separated by spaces",
                    ephemeral=True,
                )

    class AnswerButton(discord.ui.Button):
        async def callback(self, interaction):
            await interaction.response.send_modal(
                AnswerModal(discord.ui.InputText(label="Answer"), title=game.current_round.acro)
            )

    view.add_item(AnswerButton(label="Answer", style=discord.ButtonStyle.green))
    return view


def acro_vote_view(game, submission):
    view = discord.ui.View(timeout=None)

    class VoteButton(discord.ui.Button):
        async def callback(self, interaction):
            await interaction.response.defer()
            try:
                await game.add_vote(interaction.user, self.custom_id)
            except Round.SubmissionDoesNotExistError:
                await interaction.followup.send(
                    "That submission does not exist! Maybe the round is over?", ephemeral=True
                )
            except Round.AlreadyVotedError:
                await interaction.followup.send("You have already voted for this round!", ephemeral=True)
            except Round.CantVoteForYourselfError:
                await interaction.followup.send("You can't vote for yourself!", ephemeral=True)
            else:
                await interaction.followup.send("Your vote has been counted!", ephemeral=True)

    view.add_item(VoteButton(label="Vote", custom_id=submission.id, style=discord.ButtonStyle.primary))
    return view


@bot.slash_command(description="Start a new Acro Party!")
async def acro(ctx):
    game = get_current_game(ctx)

    if game:
        await ctx.respond("There is already a game in progress!")
        return

    game = make_and_set_new_game(ctx)

    # game join phase
    join_msg = lambda: (
        "Welcome to Acro Party! Press the Join button below to play along.\n\n"
        + "\n".join(f"{mention(p)} is playing!" for p in game.players)
    ).strip()
    interaction = await ctx.respond(join_msg(), view=game_start_view(game))
    async for _ in game.wait_til_start():
        await interaction.edit_original_response(content=join_msg())

    # game intro
    await interaction.edit_original_response(
        content=join_msg() + f"\n\nThe game has started! There will be {NUM_ROUNDS} rounds. Good luck!"
    )

    for this_round in game.create_rounds():
        submitted_players = {}

        # announce acronym
        player_msg = lambda p, is_done: f"{mention(p)} has submitted" + (
            "!" if is_done else " and is still processing..."
        )
        acro_msg = lambda: (
            (
                f"Round {game.current_round_no} of {game.num_rounds}. Your acronym is **`{this_round.acro}`**\n\n"
                + "\n".join(player_msg(*i) for i in submitted_players.items())
            ).strip()
            + f"\n\nWaiting on {game.num_submissions_remaining} more submissions..."
        )
        msg = await interaction.followup.send(acro_msg(), view=acro_answer_view(game=game))

        # wait for submissions
        async for (player, waiting) in game.wait_submissions():
            submitted_players[player] = waiting
            await msg.edit(content=acro_msg())

        msg = await msg.reply(f"Round {game.current_round_no} submissions in! Here they are")

        # voting phase, for now just display all the images
        for submission in game.current_round.submissions:
            await ctx.channel.send(
                file=discord.File(submission.submission_img_bytesio, filename="submission.png"),
                view=acro_vote_view(game, submission),
            )

        voting_phase = lambda: (
            (
                "Get your votes in! Vote for the best image, though you can't vote for your own. Vote for the best image and you win an extra point\n\n"
                + "\n".join(f"Player {mention(player)} has voted" for player in game.current_round.all_voters)
            ).strip()
            + f"\n\nWaiting on {game.num_votes_remaining} votes..."
        )

        msg = await ctx.channel.send(voting_phase())
        async for _ in game.wait_votes():
            await msg.edit(content=voting_phase())

        # display vote results
        await ctx.channel.send(
            f"Round {game.current_round_no} is over! Here are the results, with the winner listed first:"
        )
        for submission in game.current_round.submissions:
            await ctx.channel.send(
                f"{submission.num_votes} votes for {mention(submission.player)}: {submission.submission_txt}",
                file=discord.File(submission.submission_img_bytesio, filename="submission.png"),
            )

    await ctx.respond(
        "The game has ended! Here are the results:\n\n" + format_winners(game.winners) + "\n\nThanks for playing!"
    )


def format_winners(winners):
    return "\n".join(
        f"{i + 1}. {mention(player)} with {num_points} points" for i, (player, num_points) in enumerate(winners)
    )


@bot.slash_command(description="Abort the current Acro Party")
async def acro_abort(ctx):
    remove_current_game(ctx)
    await ctx.respond("Game aborted!")


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")


if __name__ == "__main__":
    # TODO: print a message when the bot connects
    bot.run(open(".discord-key").read().strip())
