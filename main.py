import asyncio
import io
import itertools
import os
import random
import string
import typing

from stability_sdk import client
import stability_sdk.interfaces.gooseai.generation.generation_pb2 as generation

import discord

NUM_ROUNDS = 2

bot = discord.Bot()

_current_game: typing.Optional["Game"] = None


def mention(user):
    return f"<@{user.id}>"


class Game:
    def __init__(self, initial_player, game_channel, num_rounds=NUM_ROUNDS):
        self.players = [initial_player]
        self.num_rounds = num_rounds
        self.is_done = False

        self._rounds = []
        self._submission_q = asyncio.Queue()
        self._vote_q = asyncio.Queue()
        self._game_channel = game_channel

    @property
    def current_round_no(self):
        return len(self._rounds)

    @property
    def current_round(self):
        return self._rounds[-1]

    def add_player(self, player):
        self.players.append(player)

    async def add_submission(self, player, submission):
        await self.current_round.add_submission(player, submission)
        await self._submission_q.put(player)

    async def wait_submissions(self):
        for _ in range(len(self.players)):
            next_player = await self._submission_q.get()
            yield next_player
            self._submission_q.task_done()

    async def add_vote(self, player, submission_id):
        self.current_round.add_vote(player, submission_id)
        await self._vote_q.put(player)

    async def wait_votes(self):
        for _ in range(len(self.players)):
            next_player = await self._vote_q.get()
            yield next_player

    def start(self):
        self.next_round()

    def next_round(self):
        self._rounds.append(Round.gen_with_acro())

    @property
    def num_submissions_remaining(self):
        return len(self.players) - self.current_round.num_submissions

    @property
    def num_votes_remaining(self):
        return len(self.players) - self.current_round.num_votes


def get_current_game(ctx):
    global _current_game
    # TODO: we check ctx's current channel, and have a game per channel
    return _current_game


def make_and_set_new_game(ctx):
    global _current_game
    _current_game = Game(ctx.author, ctx.channel)


class Round:
    ACRO_LEN_MIN = 3
    ACRO_LEN_MAX = 6

    @classmethod
    def gen_with_acro(cls):
        return cls(gen_acro(cls.ACRO_LEN_MIN, cls.ACRO_LEN_MAX))

    def __init__(self, acro):
        self.acro = acro
        self.submissions = []

    @property
    def num_submissions(self):
        return len(self.submissions)

    async def add_submission(self, player, submission_text):
        if "".join(word[0] for word in submission_text.strip().split(" ")).upper() != self.acro:
            raise self.SubmissionDoesNotMatchAcroError
        self.submissions.append(await Submission.gen_img(player, submission_text))

    def add_vote(self, player, submission_id):
        if player.id in self.all_voter_user_ids:
            raise self.AlreadyVotedError
        for submission in self.submissions:
            if submission.id == submission_id:
                if player.id == submission.player.id:
                    raise self.CantVoteForYourselfError

                submission.add_vote(player)
                return
        raise self.SubmissionDoesNotExistError

    @property
    def num_votes(self):
        return sum(submission.num_votes for submission in self.submissions)

    @property
    def all_voter_user_ids(self):
        return itertools.chain(*(submission.voter_user_ids for submission in self.submissions))

    class SubmissionDoesNotMatchAcroError(Exception):
        pass

    class SubmissionDoesNotExistError(Exception):
        pass

    class AlreadyVotedError(Exception):
        pass

    class CantVoteForYourselfError(Exception):
        pass


class Submission:
    def __init__(self, player, submission_txt, submission_img_bytes, id):
        self.player = player
        self.submission_txt = submission_txt
        self.submission_img_bytes = submission_img_bytes
        self.id = id

        self._voted_by_users = []

    @property
    def submission_img_bytesio(self):
        return io.BytesIO(self.submission_img_bytes)

    @property
    def voter_user_ids(self):
        return [user.id for user in self._voted_by_users]

    @classmethod
    async def gen_img(cls, player, submission_txt):
        submission_img_bytes = await gen_img(submission_txt)
        id = "".join(random.choices(string.ascii_letters + string.digits, k=8))
        return cls(player, submission_txt, submission_img_bytes, id)

    @property
    def num_votes(self):
        return len(self._voted_by_users)

    def add_vote(self, player):
        self._voted_by_users.append(player)


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


def gen_acro(low_range, hi_range):
    return "".join(random.choice(string.ascii_uppercase) for _ in range(random.randint(low_range, hi_range)))


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
        f"The game has started! Today's contestants are: \n"
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


os.environ["STABILITY_HOST"] = "grpc.stability.ai:443"
os.environ["STABILITY_KEY"] = open(".dreamstudio-key").read().strip()

stability_api = client.StabilityInference(
    key=os.environ["STABILITY_KEY"],
    verbose=True,
)


# returns a `bytes` with the image png data
async def gen_img(prompt):
    def gen_img_blocking(prompt):
        answers = stability_api.generate(prompt)
        for answer in answers:
            for artifact in answer.artifacts:
                if artifact.type == generation.ARTIFACT_IMAGE:
                    return artifact.binary
                if artifact.finish_reason == generation.FILTER:
                    raise Exception(
                        "Your request activated the API's safety filters and could not be processed."
                        "Please modify the prompt and try again."
                    )

    return await asyncio.to_thread(gen_img_blocking, prompt)


# TODO: print a message when the bot connects
bot.run(open(".discord-key").read().strip())
