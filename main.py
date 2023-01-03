import os
import typing

import discord

from game import NUM_ROUNDS, Game, Round

bot = discord.Bot()

_current_game: typing.Optional["Game"] = None


def mention(user):
    return f"<@{user.id}>"


def get_current_game(ctx):
    global _current_game
    # TODO: we check ctx's current channel, and have a game per channel
    return _current_game


def make_and_set_new_game(ctx):
    global _current_game
    _current_game = Game(ctx.author, ctx.channel)


@bot.slash_command(description="Make a new Acro Party!")
async def acro_new(ctx):
    game = get_current_game(ctx)

    if game:
        await ctx.respond("There is already a game in progress!")
        return
    make_and_set_new_game(ctx)

    # TODO: move game loop here instead of in acro_start
    # for starters, then we can easily edit this message to show all players who have joined the game, instead of
    # spamming the channel with a message for each player, also that way it's harder to tell who all is playing
    #
    # then maybe later we can have a "Join" button to make it easy to join the game
    await ctx.respond("Welcome to Acro Party! Type `/acro_join` before the game starts to play along.")


@bot.slash_command(description="Join the current Acro Party!")
async def acro_join(ctx):
    if not (game := get_current_game(ctx)):
        await ctx.respond("There is no game in progress!", ephemeral=True)
        return
    game.add_player(ctx.author)
    await ctx.respond(f"A NEW CHALLENGER HAS APPEARED! {mention(ctx.author)}")


NEWLINE = "\n"


@bot.slash_command(description="Abort the current Acro Party")
async def acro_abort(ctx):
    global _current_game
    _current_game = None
    await ctx.respond("Game aborted!")


# TODO: it's really confusing, the diff. between acro_start and acro_new. in the final version, we should show an
# ephemeral message to the game starter, allowing them to click a Button to just go and start the game, removing the
# need for us to have two separate commands
@bot.slash_command(description="Start the Acro Party!")
async def acro_start(ctx):
    if not (game := get_current_game(ctx)):
        await ctx.respond("There is no game in progress", ephemeral=True)
        return

    # game intro
    game.start()
    await ctx.respond(
        f"The game has started! Today's contestants are:\n"
        f"{NEWLINE.join(f'- {mention(player)}' for player in game.players)}.\n"
        f"Wish them luck!\n"
        "\n"
        # might add future instructions for how the game works here
        f"There will be {NUM_ROUNDS} rounds."
    )

    while not game.is_done:
        this_round = game.current_round

        # announce acronym
        await ctx.respond(
            f"Round {game.current_round_no} of {game.num_rounds}. Your acronym is **`{this_round.acro}`**. Type `/acro_submit <your answer>` to submit your answer."
        )

        # wait for submissions
        async for player in game.wait_submissions():
            # TODO: it's going to say waiting on 0 more which sounds weird
            await ctx.respond(
                f"Player {mention(player)} has submitted. Waiting on {game.num_submissions_remaining} more"
            )

        # voting phase, for now just display all the images
        for submission in game.current_round.submissions:
            await ctx.respond(
                f"Type `/acro_vote {submission.id}` to vote for the following",
                file=discord.File(submission.submission_img_bytesio, filename="submission.png"),
            )

        # TODO: probably going to want to have the voting phase time out, i believe we can just add the logic in wait_votes()
        async for player in game.wait_votes():
            await ctx.respond(f"Player {mention(player)} has voted. Waiting on {game.num_votes_remaining} more")

        # display vote results
        await ctx.respond(f"Round {game.current_round_no} is over! Here are the results, with the winner listed first:")
        for submission in game.current_round.submissions:
            await ctx.respond(
                f"{submission.num_votes} votes for {mention(submission.player)}: {submission.submission_txt}",
                file=discord.File(submission.submission_img_bytesio, filename="submission.png"),
            )

        game.next_round()

    await ctx.respond("The game has ended! Thanks for playing!")


@bot.slash_command(description="Submit your answer to the current Acro Party")
async def acro_submit(ctx, answer: str):
    if not (game := get_current_game(ctx)):
        await ctx.respond("There is no game in progress")
        return
    await ctx.defer(ephemeral=True)

    try:
        await game.add_submission(ctx.author, answer)
    except Round.SubmissionDoesNotMatchAcroError:
        await ctx.followup.send(
            "Your answer does not match the acronym! Make sure every word starts with the following letters: "
            f"**`{game.current_round.acro}`**, separated by spaces"
        )
    else:
        await ctx.followup.send("Your answer has been submitted!")


# TODO: voting should totally be by button-press instead of command. so this handler will probably be gone once we
# polish this up
@bot.slash_command(description="Vote for the best answer to the current acro")
async def acro_vote(ctx, submission_id: str):
    if not (game := get_current_game(ctx)):
        await ctx.respond("There is no game in progress")
        return
    await ctx.defer(ephemeral=True)

    try:
        await game.add_vote(ctx.author, submission_id)
    except Round.SubmissionDoesNotExistError:
        await ctx.followup.send(
            "That submission does not exist! Make sure you are voting for a submission that has been submitted and type its ID precisely."
        )
    except Round.AlreadyVotedError:
        await ctx.followup.send("You have already voted for this round!")
    except Round.CantVoteForYourselfError:
        await ctx.followup.send("You can't vote for yourself!")
    else:
        await ctx.followup.send("Your vote has been submitted!")


if __name__ == '__main__':
    # TODO: print a message when the bot connects
    bot.run(open(".discord-key").read().strip())
