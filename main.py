import io
import os
from stability_sdk import client
import stability_sdk.interfaces.gooseai.generation.generation_pb2 as generation
from PIL import Image

import discord

bot = discord.Bot()


@bot.slash_command(description="Generate an image from a prompt")
async def make_art(ctx, prompt: str):
    await ctx.defer()
    img = generate_img(prompt)
    img.save("image.png")
    await ctx.followup.send(f"art for {prompt}", file=discord.File("image.png"))


os.environ["STABILITY_HOST"] = "grpc.stability.ai:443"
os.environ["STABILITY_KEY"] = open(".dreamstudio-key").read().strip()

stability_api = client.StabilityInference(
    key=os.environ["STABILITY_KEY"],
    verbose=True,
)


def generate_img(prompt):
    answers = stability_api.generate(prompt)
    for answer in answers:
        for artifact in answer.artifacts:
            if artifact.type == generation.ARTIFACT_IMAGE:
                return Image.open(io.BytesIO(artifact.binary))
            if artifact.finish_reason == generation.FILTER:
                raise Exception(
                    "Your request activated the API's safety filters and could not be processed."
                    "Please modify the prompt and try again."
                )


bot.run(open(".discord-key").read().strip())
