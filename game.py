import asyncio
import io
import itertools
import os
import random
import string

from stability_sdk import client
from stability_sdk.interfaces.gooseai.generation import generation_pb2 as generation

NUM_ROUNDS = 2
os.environ["STABILITY_HOST"] = "grpc.stability.ai:443"
os.environ["STABILITY_KEY"] = open(".dreamstudio-key").read().strip()


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


def gen_acro(low_range, hi_range):
    return "".join(random.choice(string.ascii_uppercase) for _ in range(random.randint(low_range, hi_range)))


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
