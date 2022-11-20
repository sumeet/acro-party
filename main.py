import asyncio
import io
import os
import random
import string
import typing

from stability_sdk import client
import stability_sdk.interfaces.gooseai.generation.generation_pb2 as generation

import discord

NUM_ROUNDS = 10

bot = discord.Bot()

# <player-1> /acro_party_new
# <bot> Welcome to Acro Party! Type /acro_party_join before the game starts to play along. Type /acro_party_start to start the game.
# // Note: starting implicitly joins, so player-1 will automatically be part of the game, since they started it
# <player-2> /acro_party_join
# <bot> A NEW CHALLENGER HAS APPEARED! <player-2>
# <player-3> /acro_party_join
# <bot> A NEW CHALLENGER HAS APPEARED! <player-3>
# <player-1> /acro_party_start
# <bot> The game has started! Today's contestants are: <player-1>, <player-2>, <player-3>. Wish them luck!
# <bot> There will be 10 rounds.

_current_game: typing.Optional["Game"] = None


def mention(user):
    return f"<@{user.id}>"


class Game:
    def __init__(self, initial_player, game_channel, num_rounds=NUM_ROUNDS):
        self.players = [initial_player]
        self.num_rounds = num_rounds
        self.is_done = False

        self._rounds = []
        self._event_queue = asyncio.Queue()
        self._game_channel = game_channel

    @property
    def current_round_no(self):
        return len(self._rounds)

    def add_player(self, player):
        self.players.append(player)

    def start_game(self):
        self._rounds.append(Round())

    def add_response(self, player, response):
        self.responses[player] = response


def get_current_game(ctx):
    global _current_game
    # TODO: we check ctx's current channel, and have a game per channel
    return _current_game


def make_and_set_new_game(ctx):
    global _current_game
    _current_game = Game(ctx.author, ctx.channel)


class Round:
    def __init__(self, acro):
        self.acro = acro
        self.responses = []

    async def add_response(self, player, response_text):
        if "".join(word[0] for word in response_text.strip().split(" ")).upper() != self.acro:
            raise self.ResponseDoesNotMatchAcroError
        self.responses.append(await Response.gen_img(player, response_text))

    class ResponseDoesNotMatchAcroError(Exception):
        pass

    ACRO_LEN_MIN = 4
    ACRO_LEN_MAX = 6

    @classmethod
    def gen_with_acro(cls):
        return cls(gen_acro(cls.ACRO_LEN_MIN, cls.ACRO_LEN_MAX))


class Response:
    def __init__(self, player, response_txt, response_img):
        self.player = player
        self.response_txt = response_txt
        self.response_img = response_img
        self.num_votes = 0

    @classmethod
    async def gen_img(cls, player, response_txt):
        response_img = await gen_img(response_txt)
        return cls(player, response_txt, response_img)


@bot.slash_command(description="Make a new Acro Party!")
async def acro_new(ctx):
    game = get_current_game(ctx)

    if game:
        await ctx.respond("There is already a game in progress!")
        return
    make_and_set_new_game(ctx)
    await ctx.respond("Welcome to Acro Party! Type `/acro_join` before the game starts to play along.")


@bot.slash_command(description="Join the current Acro Party!")
async def acro_join(ctx):
    if not (game := get_current_game(ctx)):
        await ctx.respond("There is no game in progress!")
        return
    game.add_player(ctx.author)
    await ctx.respond(f"A NEW CHALLENGER HAS APPEARED! {mention(ctx.author)}")


def gen_acro(low_range, hi_range):
    return "".join(random.choice(string.ascii_uppercase) for _ in range(random.randint(low_range, hi_range)))


NEWLINE = "\n"


@bot.slash_command(description="Start the Acro Party!")
async def acro_start(ctx):
    if not (game := get_current_game(ctx)):
        await ctx.respond("There is no game in progress")
        return

    # game intro
    await ctx.respond(
        f"The game has started! Today's contestants are: \n"
        f"{NEWLINE.join(f'- {mention(player)}' for player in current_game.players)}.\n"
        f"Wish them luck!\n"
        "\n"
        # might add future instructions for how the game works here
        f"There will be {NUM_ROUNDS} rounds."
    )

    while not game.is_done:
        await ctx.respond(
            f"Round {game.current_round_no} of {game.num_rounds}. Your acronym is **`{current_game.acro}`**. Type `/acro_submit <your answer>` to submit your answer."
        )
        # png_bytes = gen_img(prompt)
        # await ctx.respond(file=discord.File(png_bytes, "image.png"))

    await ctx.respond("The game has ended! Thanks for playing!")


@bot.slash_command(description="Submit your answer to the current Acro Party")
async def acro_party_submit(ctx, answer: str):
    global current_game

    if current_game is None:
        await ctx.respond("There is no game in progress")
        return
    current_game.add_response(ctx.author, answer)
    await ctx.respond(f"{mention(ctx.author)} has submitted their answer")


os.environ["STABILITY_HOST"] = "grpc.stability.ai:443"
os.environ["STABILITY_KEY"] = open(".dreamstudio-key").read().strip()

stability_api = client.StabilityInference(
    key=os.environ["STABILITY_KEY"],
    verbose=True,
)


async def gen_img(prompt):
    def gen_img_blocking(prompt):
        answers = stability_api.generate(prompt)
        for answer in answers:
            for artifact in answer.artifacts:
                if artifact.type == generation.ARTIFACT_IMAGE:
                    return io.BytesIO(artifact.binary)
                if artifact.finish_reason == generation.FILTER:
                    raise Exception(
                        "Your request activated the API's safety filters and could not be processed."
                        "Please modify the prompt and try again."
                    )

    return asyncio.to_thread(gen_img_blocking, prompt)


bot.run(open(".discord-key").read().strip())
