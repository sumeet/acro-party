import asyncio
import io
import itertools
import os
import random
import string
from collections import defaultdict
from dataclasses import dataclass

from stability_sdk import client
from stability_sdk.interfaces.gooseai.generation import generation_pb2 as generation

NUM_ROUNDS = 2
os.environ["STABILITY_HOST"] = "grpc.stability.ai:443"
os.environ["STABILITY_KEY"] = open(".dreamstudio-key").read().strip()


class ScoreBreakdown:
    def __init__(self):
        self._scores = []

    def add(self, score):
        self._scores.append(score)

    @property
    def total_points(self):
        return sum(s.num_points for s in self._scores)

    @property
    def total_points_str(self):
        return f"{self.total_points} points" if self.total_points != 1 else "1 point"

    def __str__(self):
        return " + ".join(map(str, self._scores)) + f" = {self.total_points_str}"


@dataclass
class ScoreByVote:
    num_votes: int

    def __str__(self):
        if self.num_votes == 1:
            return "1 point from a vote"
        return f"{self.num_votes} points from votes"

    @property
    def num_points(self):
        return self.num_votes


class ScoreByVotingForWinner:
    def __str__(self):
        return "1 point for voting for the winner"

    @property
    def num_points(self):
        return 1


class ScoreVotedForSelf:
    def __str__(self):
        return "1 point deducted for voting for themselves"

    @property
    def num_points(self):
        return -1


class Game:
    def __init__(self, initial_player, game_channel, num_rounds=NUM_ROUNDS):
        self.players = [initial_player]
        self.num_rounds = num_rounds

        self._rounds = []
        self._submission_q = asyncio.Queue()
        self._vote_q = asyncio.Queue()
        self._join_q = asyncio.Queue()
        self._game_channel = game_channel

    @property
    def winners(self):
        num_votes_by_player = defaultdict(int)
        for round in self._rounds:
            for player, breakdown in round.score_breakdown.items():
                num_votes_by_player[player] += breakdown.total_points
        return sorted(num_votes_by_player.items(), key=lambda x: x[1], reverse=True)

    @property
    def current_round_no(self):
        return len(self._rounds)

    @property
    def current_round(self):
        return self._rounds[-1]

    class PlayerAlreadyJoinedError(Exception):
        pass

    async def add_player(self, player):
        if player in self.players:
            raise self.PlayerAlreadyJoinedError
        self.players.append(player)
        await self._join_q.put(player)

    async def wait_til_start(self):
        while True:
            player = await self._join_q.get()
            self._join_q.task_done()
            if player:
                yield player
            else:
                break

    # returns tuple of (player, is_waiting) bc stable diffusion can be slow
    async def add_submission(self, player, submission):
        await self._submission_q.put((player, False))
        await self.current_round.add_submission(player, submission)
        await self._submission_q.put((player, True))

    async def wait_submissions(self):
        # * 2 because one for is_waiting = True, and another for False
        for _ in range(len(self.players) * 2):
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

    async def start(self):
        await self._join_q.put(None)

    def create_rounds(self):
        for _ in range(self.num_rounds):
            round = Round.gen_with_acro()
            self._rounds.append(round)
            yield round

    @property
    def num_submissions_remaining(self):
        return len(self.players) - self.current_round.num_submissions

    @property
    def num_votes_remaining(self):
        return len(self.players) - self.current_round.num_votes

    class IsOverError(Exception):
        pass


class Round:
    ACRO_LEN_MIN = 3
    ACRO_LEN_MAX = 6

    @property
    def score_breakdown(self):
        score_by_player = defaultdict(ScoreBreakdown)
        for submission in self.submissions:
            num_other_voters = 0
            for voter in submission.voters:
                if voter == submission.player:
                    score_by_player[voter].add(ScoreVotedForSelf())
                else:
                    num_other_voters += 1
            if num_other_voters > 0:
                score_by_player[submission.player].add(ScoreByVote(num_other_voters))
        for voter in self.winning_submission.voters:
            score_by_player[voter].add(ScoreByVotingForWinner())
        return score_by_player

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
                submission.add_vote(player)
                return
        raise self.SubmissionDoesNotExistError

    @property
    def num_votes(self):
        return sum(submission.num_votes for submission in self.submissions)

    @property
    def winning_submission(self):
        return max(self.submissions, key=lambda x: x.num_votes)

    @property
    def all_voter_user_ids(self):
        return itertools.chain(*(submission.voter_user_ids for submission in self.submissions))

    @property
    def all_voters(self):
        return itertools.chain(*(submission.voters for submission in self.submissions))

    class SubmissionDoesNotMatchAcroError(Exception):
        pass

    class SubmissionDoesNotExistError(Exception):
        pass

    class AlreadyVotedError(Exception):
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

    @property
    def voters(self):
        return self._voted_by_users

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
