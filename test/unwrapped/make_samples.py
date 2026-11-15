"""Build the fabricated JSON samples used by test_unwrapped.py.

No network access and no real credentials are involved: every id, name and
text is synthetic. Run from the repository root:

    python -I test/unwrapped/make_samples.py
"""

import base64
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLES = os.path.join(HERE, "samples")

MAX_DEPTH = 5


def make_user(rest_id, screen_name):
    # Minimal object that still satisfies the strict User schema; `id` is a
    # base64-encoded token and UserCore requires created_at/name/screen_name.
    token = base64.b64encode(f"User:{rest_id}".encode("ascii")).decode("ascii")
    return {
        "__typename": "User",
        "id": token,
        "rest_id": rest_id,
        "is_blue_verified": False,
        "profile_image_shape": "Circle",
        "core": {
            "created_at": "Mon Jan 01 00:00:00 +0000 2024",
            "name": screen_name,
            "screen_name": screen_name,
        },
    }


def make_legacy(rest_id, text, user_id, retweeted=None):
    legacy = {
        "bookmark_count": 0,
        "bookmarked": False,
        "conversation_id_str": rest_id,
        "created_at": "Mon Jan 01 00:00:00 +0000 2024",
        "display_text_range": [0, len(text)],
        "favorite_count": 0,
        "favorited": False,
        "full_text": text,
        "is_quote_status": False,
        "lang": "en",
        "quote_count": 0,
        "reply_count": 0,
        "retweet_count": 1 if retweeted is not None else 0,
        "retweeted": False,
        "user_id_str": user_id,
        "id_str": rest_id,
    }
    if retweeted is not None:
        legacy["retweeted_status_result"] = {
            "__typename": "TimelineTweet",
            "result": retweeted,
        }
    return legacy


def make_tweet(rest_id, text, user_id, screen_name, retweeted=None, quote=None):
    tweet = {
        "__typename": "Tweet",
        "rest_id": rest_id,
        "core": {"user_results": {"result": make_user(user_id, screen_name)}},
        "legacy": make_legacy(rest_id, text, user_id, retweeted=retweeted),
    }
    if quote is not None:
        tweet["quoted_status_result"] = {
            "__typename": "TimelineTweet",
            "result": quote,
        }
    return tweet


def wrap_timeline_tweet(result):
    return {
        "__typename": "TimelineTweet",
        "itemType": "TimelineTweet",
        "tweetDisplayType": "Tweet",
        "tweet_results": {"__typename": "TimelineTweet", "result": result},
    }


def write(name, data):
    path = os.path.join(SAMPLES, name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    print(f"wrote {path}")


def build_chain(length):
    """length retweet wrappers: ids 9000 (outermost) .. 900{length}."""
    inner = make_tweet(
        f"9{length:03d}",
        f"inner tweet {length}",
        f"8{length:03d}",
        f"user{length}",
    )
    for depth in range(length - 1, -1, -1):
        inner = make_tweet(
            f"9{depth:03d}",
            f"retweet layer {depth}",
            f"8{depth:03d}",
            f"user{depth}",
            retweeted=inner,
        )
    return wrap_timeline_tweet(inner)


def main():
    # 01: plain tweet with a quote. The quote stays a field of the subject.
    quoted = make_tweet("3001", "quoted tweet", "2002", "bob")
    quote_tweet = make_tweet("1001", "plain tweet", "2001", "alice", quote=quoted)
    write("01_quote_input.json", wrap_timeline_tweet(quote_tweet))

    # 02: TweetWithVisibilityResults wrapping a retweet (two wrapper kinds).
    original = make_tweet("1102", "original body", "2102", "orig")
    retweeter = make_tweet(
        "1101",
        "RT @orig: original body",
        "2101",
        "retweeter",
        retweeted=original,
    )
    visibility = {
        "__typename": "TweetWithVisibilityResults",
        "tweet": retweeter,
    }
    write("02_visibility_retweet_input.json", wrap_timeline_tweet(visibility))

    # 03: tombstone - unfolded but no body available.
    write(
        "03_tombstone_input.json",
        wrap_timeline_tweet(
            {
                "__typename": "TweetTombstone",
                "tombstone_info": {"text": "This Post was deleted by the Post author."},
            }
        ),
    )

    # 04: unavailable with a reason (e.g. NSFW when logged out).
    write(
        "04_unavailable_input.json",
        wrap_timeline_tweet(
            {"__typename": "TweetUnavailable", "reason": "NsfwLoggedOut"}
        ),
    )

    # 05: 7 tweets -> 6 retweet hops, one more than TweetUnwrappedMaxDepth.
    write("05_over_depth_input.json", build_chain(MAX_DEPTH + 1))
    # 06: repeated rest_id along the chain (an id-cycle, the JSON-encodable
    # equivalent of self-referential data). Both the depth bound and a
    # seen-rest_id guard must stop the unfold from looping forever.
    inner = make_tweet("7001", "self referencing retweet", "6001", "loopy")
    for _ in range(MAX_DEPTH + 1):
        inner = make_tweet(
            "7001",
            "self referencing retweet",
            "6001",
            "loopy",
            retweeted=inner,
        )
    write("06_self_reference_input.json", wrap_timeline_tweet(inner))

    # 07: a full (fabricated) TweetDetailResponse carrying the response-level
    # tweet_unwrapped view next to the raw threaded conversation.
    focal = make_tweet("5001", "focal tweet", "4001", "carol")
    timeline_item = {
        "__typename": "TimelineTimelineItem",
        "entryType": "TimelineTimelineItem",
        "itemContent": wrap_timeline_tweet(focal),
    }
    detail_response = {
        "data": {
            "threaded_conversation_with_injections_v2": {
                "instructions": [
                    {
                        "type": "TimelineAddEntries",
                        "entries": [
                            {
                                "entryId": "tweet-5001",
                                "sortIndex": "5001",
                                "content": timeline_item,
                            }
                        ],
                    }
                ]
            },
            "tweet_unwrapped": {
                "state": "Available",
                "depth": 0,
                "tweet": focal,
            },
        }
    }
    write("07_tweet_detail_response_input.json", detail_response)

    # 08: TweetResultByRestId where the result is an unavailable tweet.
    by_rest_id_response = {
        "data": {
            "tweetResult": {
                "__typename": "TimelineTweet",
                "result": {
                    "__typename": "TweetUnavailable",
                    "reason": "Protected",
                },
            },
            "tweet_unwrapped": {
                "state": "Unavailable",
                "__typename": "TweetUnavailable",
                "reason": "Protected",
                "depth": 0,
            },
        }
    }
    write("08_tweet_result_by_rest_id_input.json", by_rest_id_response)

    # 09: TweetPreviewDisplay - unavailable as a full tweet but it still
    # carries a reduced preview; the unwrap view must keep __typename and
    # surface that preview instead of a body.
    preview_tweet = {
        "rest_id": "6001",
        "text": "limited preview text",
        "core": {
            "user_results": {
                "result": make_user("4999", "dave"),
            }
        },
        "entities": {},
        "reply_count": 0,
        "retweet_count": 0,
        "favorite_count": 0,
        "bookmark_count": 0,
        "quote_count": 0,
        "view_count": {"count": "0"},
        "created_at": "Mon Jan 01 00:00:00 +0000 2024",
    }
    preview_display = {
        "__typename": "TweetPreviewDisplay",
        "tweet": preview_tweet,
        "limited_action_results": {"limited_actions": []},
        "cta": {
            "title": "View",
            "url": {
                "url": "https://example.invalid/view/6001",
                "urlType": "ExternalUrl",
            },
        },
    }
    write("09_preview_display_input.json", wrap_timeline_tweet(preview_display))


if __name__ == "__main__":
    main()
