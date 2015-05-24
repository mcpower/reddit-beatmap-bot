import re
import html
import praw
import requests
import configparser
import os
import urllib.parse
import time
from functools import lru_cache
from limitedset import LimitedSet


if not os.path.exists("config.ini"):
    print("No config file found.")
    print("Copy config_example.ini to config.ini and modify to your needs.")
    exit()

config = configparser.ConfigParser()
config.read("config.ini")

MAX_COMMENTS = int(config.get("bot", "max_comments"))
OSU_CACHE = int(config.get("bot", "osu_cache"))
URL_REGEX = re.compile(r'<a href="(?P<url>https?://osu\.ppy\.sh/[^"]+)">(?P=url)</a>')  # NOQA


@lru_cache(maxsize=OSU_CACHE)
def get_beatmap_info(map_type, map_id):
    """Gets information about a beatmap given a URL.

    Cached helper function to try to minimize osu! api requests.
    """
    payload = {"k": config.get("osu", "api_key"), map_type: map_id}
    r = requests.get("https://osu.ppy.sh/api/get_beatmaps", params=payload)
    out = r.json()
    if "error" in out:
        raise Exception("osu!api returned an error of " + out["error"])
    return out


def seconds_to_string(seconds):
    """Returns a m:ss representation of a time in seconds."""
    return "{0}:{1:0>2}".format(*divmod(seconds, 60))


def get_map_params(url):
    """Returns a tuple of (map_type, map_id) or False if URL is invalid.

    Possible URL formats:
        https://osu.ppy.sh/p/beatmap?b=115891&m=0#
        https://osu.ppy.sh/b/244182
        https://osu.ppy.sh/p/beatmap?s=295480
        https://osu.ppy.sh/s/295480
    """
    parsed = urllib.parse.urlparse(url)

    if parsed.path.startswith("/b/"):
        return ("b", parsed.path[3:])
    elif parsed.path.startswith("/s/"):
        return ("s", parsed.path[3:])
    elif parsed.path == "/p/beatmap":
        query = urllib.parse.parse_qs(parsed.query)
        if "b" in query:
            return ("b", query["b"][0])
        elif "s" in query:
            return ("s", query["s"][0])
    return False


def format_map(tup):
    """Formats a map for a comment given its type and id."""
    map_type, map_id = tup
    info = dict(get_beatmap_info(map_type, map_id)[0])  # create new instance
    info["difficultyrating"] = float(info["difficultyrating"])
    info["hit_length"] = seconds_to_string(int(info["hit_length"]))
    info["total_length"] = seconds_to_string(int(info["total_length"]))

    if map_type == "b":  # single map
        return config.get("template", "map").format(**info)
    if map_type == "s":  # beatmap set
        return config.get("template", "mapset").format(**info)


def format_comment(maps):
    """Formats a list of (map_type, map_id) tuples into a comment."""
    seen = set()
    maps_without_dups = []
    for beatmap in maps:
        if beatmap not in seen:
            seen.add(beatmap)
            maps_without_dups.append(beatmap)

    return "{0}\n\n{1}\n\n{2}".format(
        config.get("template", "header"),
        "\n\n".join(map(format_map, maps_without_dups)),
        config.get("template", "footer")
    )


def get_maps_from_string(string):
    """Extracts all valid maps as (map_type, map_id) in an HTML string."""
    return list(filter(None, map(get_map_params,
                                 URL_REGEX.findall(html.unescape(string)))))


def has_replied(comment, r):
    """Checks whether the bot has replied to a comment already.

    Apparently costly.
    Taken from http://www.reddit.com/r/redditdev/comments/1kxd1n/_/cbv4usl"""
    botname = config.get("reddit", "username")
    return any(reply.author.name == botname for reply in
               r.get_submission(comment.permalink).comments[0].replies)


def reply(comment, text):
    print("Replying to {c.author.name}, comment id {c.id}".format(c=comment))
    print("###")
    print(text)
    print("###")
    comment.reply(text)

r = praw.Reddit(user_agent=config.get("reddit", "user_agent"))
r.login(config.get("reddit", "username"), config.get("reddit", "password"))

seen_comments = LimitedSet(MAX_COMMENTS + 100)
subreddit = config.get("reddit", "subreddit")


while True:
    try:
        comments = r.get_comments(subreddit, limit=MAX_COMMENTS)
        for comment in comments:
            if comment.id in seen_comments:
                break  # already reached up to here before
            seen_comments.add(comment.id)
            found = get_maps_from_string(comment.body_html)
            if not found:
                print("New comment", comment.id, "with no maps.")
                continue
            if has_replied(comment, r):
                print("We've replied to {0} before!".format(comment.id))
                break  # we reached here in a past instance of this bot

            reply(comment, format_comment(found))
    except KeyboardInterrupt:
        print("Stopping the bot.")
        exit()
    except Exception as e:
        print("We caught an exception! It says:")
        print(e)
        print("Sleeping for 15 seconds.")
        time.sleep(15)
        continue
