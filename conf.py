from os import environ


DISCORD_KEY = environ.get('DISCORD_KEY') or open(".discord-key").read().strip()
DREAMSTUDIO_KEY = (environ.get('DREAMSTUDIO_KEY') or
                   open(".dreamstudio-key").read().strip())
