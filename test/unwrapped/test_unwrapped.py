"""Offline tests for the reusable "unwrapped tweet" OpenAPI path.

These tests never touch the network and use only the fabricated JSON samples
under samples/. They do two things:

1. Validate the built spec itself (dist/<profile>/openapi-3.0.yaml) against
   the JSON Schema Draft 4 dialect used by OpenAPI 3.0, and assert the new
   TweetUnwrapped* schemas are referenced by the TweetDetail-related responses
   without changing TweetUnion.

2. Re-implement the documented unwrap path (matching the schemas in
   src/openapi/schemas/tweet.yaml) and validate every produced object against
   the generated spec with jsonschema, covering:
     - a plain tweet with a quote (quote kept as a field, subject unchanged)
     - TweetWithVisibilityResults wrapping a retweet (two wrapper kinds)
     - TweetTombstone / TweetUnavailable / TweetPreviewDisplay
       ("unwrapped but unavailable", keeping __typename/reason/preview)
     - a retweet chain longer than TweetUnwrappedMaxDepth (bounded unfold)
     - self-referential data (depth bound + seen-id guard, no infinite loop)
     - full fabricated TweetDetail / TweetResultByRestId responses

Run from the repository root:

    python tools/build.py
    python -I test/unwrapped/test_unwrapped.py
"""

import json
import os
import sys

import yaml
from jsonschema import Draft4Validator, RefResolver, ValidationError, validators
from jsonschema._keywords import oneOf as _default_oneOf

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SAMPLES_DIR = os.path.join(os.path.dirname(__file__), "samples")
MAX_DEPTH = 5

UNAVAILABLE_TYPENAMES = {
    "TweetTombstone",
    "TweetUnavailable",
    "TweetPreviewDisplay",
}


def _discriminating_one_of(validator, one_of, instance, schema):
    """OpenAPI 3 discriminator-aware oneOf.

    The generated clients run with useOneOfDiscriminatorLookup: when a oneOf
    carries a discriminator the branch is chosen purely from the tag value,
    instead of testing every subschema. That matters here because permissive
    schemas such as TweetTombstone (additionalProperties: true) would
    otherwise make a full Tweet match several TweetUnion branches.
    """
    discriminator = schema.get("discriminator")
    if not discriminator:
        yield from _default_oneOf(validator, one_of, instance, schema)
        return

    property_name = discriminator["propertyName"]
    mapping = discriminator.get("mapping", {})
    tag = instance.get(property_name) if isinstance(instance, dict) else None
    target_ref = mapping.get(tag)

    for index, sub_schema in enumerate(one_of):
        ref = sub_schema.get("$ref")
        if target_ref is not None:
            matched = ref == target_ref
        else:  # implicit mapping: schema name equals the tag
            matched = bool(ref) and ref.rsplit("/", 1)[-1] == tag
        if matched:
            yield from validator.descend(instance, sub_schema, schema_path=index)
            return

    yield ValidationError(f"{property_name!r}={tag!r} matches no discriminator branch")


# Same Draft 4 dialect as OpenAPI 3.0, but oneOf follows the discriminator.
OASValidator = validators.extend(Draft4Validator, {"oneOf": _discriminating_one_of})


# --------------------------------------------------------------------------
# Reference implementation of the unwrap path documented by the schemas
# --------------------------------------------------------------------------


def unwrap_tweet(item_result, max_depth=MAX_DEPTH):
    """Unfold an ItemResult (or a raw TweetUnion) into a TweetUnwrapped.

    Rules (mirrors src/openapi/schemas/tweet.yaml):
      * TweetWithVisibilityResults -> its inner `tweet`
      * retweet -> legacy.retweeted_status_result.result
      * quote -> left untouched on the subject (quoted_status_result is a
        plain field, it never replaces the subject)
      * TweetTombstone / TweetUnavailable / TweetPreviewDisplay ->
        TweetUnwrappedUnavailable keeping __typename and reason

    Returns a tuple (unwrapped, visibility_hops) where visibility_hops counts
    the TweetWithVisibilityResults wrappers that were peeled.
    """
    result = item_result.get("result", item_result)

    typename = result.get("__typename")

    def unavailable_for(target):
        unavailable = {
            "state": "Unavailable",
            "__typename": target.get("__typename"),
            "depth": 0,
        }
        if target.get("reason") is not None:
            unavailable["reason"] = target["reason"]
        if (
            target.get("__typename") == "TweetPreviewDisplay"
            and target.get("tweet") is not None
        ):
            unavailable["preview"] = target["tweet"]
        return unavailable

    if typename in UNAVAILABLE_TYPENAMES:
        return unavailable_for(result), 0

    # TweetWithVisibilityResults: peel the wrapper to reach the inner tweet.
    visibility_hops = 0
    while typename == "TweetWithVisibilityResults":
        result = result["tweet"]
        typename = result.get("__typename")
        visibility_hops += 1
        if typename in UNAVAILABLE_TYPENAMES:
            return unavailable_for(result), visibility_hops

    node = {
        "state": "Available",
        "depth": 0,
        "tweet": result,
    }
    head = node
    seen = {result.get("rest_id")}
    current = result

    while head["depth"] < max_depth:
        retweet_result = (
            current.get("legacy", {}).get("retweeted_status_result", {}).get("result")
        )
        if retweet_result is None:
            break

        # Defensive seen-id guard: malformed/self-referential data must never
        # loop even before the numeric depth bound is reached.
        if retweet_result.get("rest_id") in seen:
            break
        seen.add(retweet_result.get("rest_id"))

        # An unavailable tweet inside the retweet chain ends the unfold there.
        inner_typename = retweet_result.get("__typename")
        if inner_typename in UNAVAILABLE_TYPENAMES:
            head["retweet"] = {
                "state": "Unavailable",
                "__typename": inner_typename,
                "depth": head["depth"] + 1,
            }
            if retweet_result.get("reason") is not None:
                head["retweet"]["reason"] = retweet_result["reason"]
            break

        if inner_typename == "TweetWithVisibilityResults":
            retweet_result = retweet_result["tweet"]

        next_node = {
            "state": "Available",
            "depth": head["depth"] + 1,
            "tweet": retweet_result,
        }
        head["retweet"] = next_node
        head = next_node
        current = retweet_result

    return node, visibility_hops


def load_sample(name):
    with open(os.path.join(SAMPLES_DIR, name), encoding="utf-8") as f:
        return json.load(f)


# --------------------------------------------------------------------------
# Spec loading / validation helpers
# --------------------------------------------------------------------------


def load_spec(profile="docs"):
    path = os.path.join(REPO_ROOT, "dist", profile, "openapi-3.0.yaml")
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def schema_for(spec, name):
    return {"$ref": f"#/components/schemas/{name}"}


def validator_for(spec, name):
    resolver = RefResolver.from_schema(spec)
    return OASValidator(schema_for(spec, name), resolver=resolver)


def validate(spec, name, instance):
    errors = sorted(
        validator_for(spec, name).iter_errors(instance), key=lambda e: e.path
    )
    if errors:
        for e in errors:
            print(f"  validation error at {list(e.path)}: {e.message}")
        raise AssertionError(f"{name} validation failed for {instance!r:.200}")


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------


def test_spec_metaschema(spec):
    # OpenAPI 3.0.x schema objects use the JSON Schema Draft 4 dialect; this
    # walks every component schema and rejects malformed schema keywords.
    Draft4Validator.check_schema(spec)
    for component in spec["components"]["schemas"].values():
        Draft4Validator.check_schema(
            {"$schema": "http://json-schema.org/draft-04/schema#", **component}
        )
    assert spec["openapi"].startswith("3.0")
    print("ok: spec is a structurally valid OpenAPI 3.0 document")


def test_all_refs_resolve(spec):
    # Every $ref and every discriminator mapping target in the merged spec
    # must point at an existing component schema. This catches the kind of
    # broken reference a generator would reject at codegen time.
    schemas = spec["components"]["schemas"]

    def walk(node):
        if isinstance(node, dict):
            if "$ref" in node:
                ref = node["$ref"]
                assert ref.startswith("#/components/schemas/"), ref
                assert ref.rsplit("/", 1)[-1] in schemas, ref
            discriminator = node.get("discriminator")
            if isinstance(discriminator, dict):
                for target in discriminator.get("mapping", {}).values():
                    assert target in schemas or target.rsplit("/", 1)[-1] in schemas, (
                        target
                    )
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(spec)
    print("ok: every $ref and discriminator mapping target resolves")


def test_tweet_union_unchanged(spec):
    tweet_union = spec["components"]["schemas"]["TweetUnion"]
    assert tweet_union["discriminator"]["propertyName"] == "__typename"
    members = {ref["$ref"].split("/")[-1] for ref in tweet_union["oneOf"]}
    assert members == {
        "Tweet",
        "TweetWithVisibilityResults",
        "TweetTombstone",
        "TweetUnavailable",
        "TweetPreviewDisplay",
    }
    print("ok: TweetUnion keeps its five members and __typename discriminator")


def test_referenced_by_tweet_detail(spec):
    schemas = spec["components"]["schemas"]
    expected_ref = "#/components/schemas/TweetUnwrapped"

    td = schemas["TweetDetailResponseData"]["properties"]["tweet_unwrapped"]
    assert td["allOf"][0]["$ref"] == expected_ref

    tr = schemas["TweetResultByRestIdData"]["properties"]["tweet_unwrapped"]
    assert tr["allOf"][0]["$ref"] == expected_ref

    tt = schemas["TimelineTweet"]["properties"]["tweet_unwrapped"]
    assert tt["allOf"][0]["$ref"] == expected_ref

    # the optional field must not become required
    assert "tweet_unwrapped" not in schemas["TimelineTweet"].get("required", [])
    assert "tweet_unwrapped" not in schemas["TweetDetailResponseData"].get(
        "required", []
    )
    print(
        "ok: TweetDetail response data, TweetResultByRestId and TimelineTweet "
        "reference TweetUnwrapped (optional)"
    )


def test_depth_chain_is_bounded(spec):
    schemas = spec["components"]["schemas"]
    for depth in range(MAX_DEPTH + 1):
        node = schemas[f"TweetUnwrappedChainDepth{depth}"]
        assert node["allOf"][0]["$ref"] == ("#/components/schemas/TweetUnwrappedTweet")
        if depth < MAX_DEPTH:
            assert "retweet" in node["properties"]
            assert node["properties"]["retweet"]["allOf"][0]["$ref"] == (
                f"#/components/schemas/TweetUnwrappedChainDepth{depth + 1}"
            )
        else:
            # terminal level has no retweet slot -> the chain cannot recurse
            assert "retweet" not in node["properties"]
    print(
        f"ok: bounded chain Depth0..Depth{MAX_DEPTH}, terminal level has no "
        "recursive slot"
    )


def test_quote_is_kept_not_unfolded(spec):
    timeline_tweet = load_sample("01_quote_input.json")
    unwrapped, visibility_hops = unwrap_tweet(timeline_tweet["tweet_results"])

    assert visibility_hops == 0
    assert unwrapped["state"] == "Available"
    assert unwrapped["depth"] == 0
    assert unwrapped["tweet"]["rest_id"] == "1001"
    assert unwrapped["tweet"]["legacy"]["full_text"] == "plain tweet"
    # quote stays on the subject, it never replaces it
    assert unwrapped["tweet"]["quoted_status_result"]["result"]["rest_id"] == "3001"
    assert "retweet" not in unwrapped

    validate(spec, "TweetUnwrapped", unwrapped)
    validate(spec, "TimelineTweet", timeline_tweet)
    print("ok: quote retained as quoted_status_result, subject unchanged")


def test_visibility_then_retweet(spec):
    timeline_tweet = load_sample("02_visibility_retweet_input.json")
    unwrapped, visibility_hops = unwrap_tweet(timeline_tweet["tweet_results"])

    assert visibility_hops == 1  # exactly one TweetWithVisibilityResults peeled
    assert unwrapped["state"] == "Available"
    assert unwrapped["tweet"]["rest_id"] == "1101"
    assert unwrapped["depth"] == 0
    retweet = unwrapped["retweet"]
    assert retweet["state"] == "Available"
    assert retweet["depth"] == 1
    assert retweet["tweet"]["rest_id"] == "1102"
    assert retweet["tweet"]["legacy"]["full_text"] == "original body"
    assert (
        retweet["tweet"]["core"]["user_results"]["result"]["core"]["screen_name"]
        == "orig"
    )
    assert "retweet" not in retweet

    validate(spec, "TweetUnwrapped", unwrapped)
    print("ok: TweetWithVisibilityResults peeled, retweet unfolded one level")


def test_tombstone(spec):
    timeline_tweet = load_sample("03_tombstone_input.json")
    unwrapped, _ = unwrap_tweet(timeline_tweet["tweet_results"])

    assert unwrapped == {
        "state": "Unavailable",
        "__typename": "TweetTombstone",
        "depth": 0,
    }
    validate(spec, "TweetUnwrapped", unwrapped)
    print("ok: tombstone reported as unwrapped-but-unavailable with __typename")


def test_unavailable_keeps_reason(spec):
    timeline_tweet = load_sample("04_unavailable_input.json")
    unwrapped, _ = unwrap_tweet(timeline_tweet["tweet_results"])

    assert unwrapped["state"] == "Unavailable"
    assert unwrapped["__typename"] == "TweetUnavailable"
    assert unwrapped["reason"] == "NsfwLoggedOut"
    validate(spec, "TweetUnwrapped", unwrapped)

    # reason is optional: an unavailable result without one still validates
    without_reason = {
        "state": "Unavailable",
        "__typename": "TweetUnavailable",
    }
    validate(spec, "TweetUnwrapped", without_reason)
    print("ok: unavailable keeps __typename + reason; reason itself optional")


def test_preview_display_keeps_preview(spec):
    timeline_tweet = load_sample("09_preview_display_input.json")
    unwrapped, _ = unwrap_tweet(timeline_tweet["tweet_results"])

    assert unwrapped["state"] == "Unavailable"
    assert unwrapped["__typename"] == "TweetPreviewDisplay"
    assert "reason" not in unwrapped
    assert unwrapped["preview"]["rest_id"] == "6001"
    assert unwrapped["preview"]["text"] == "limited preview text"
    validate(spec, "TweetUnwrapped", unwrapped)
    print("ok: preview display reported unavailable but keeps reduced preview")


def test_over_depth_chain(spec):
    timeline_tweet = load_sample("05_over_depth_input.json")
    unwrapped, _ = unwrap_tweet(timeline_tweet["tweet_results"], MAX_DEPTH)

    head = unwrapped
    ids = []
    while True:
        ids.append(head["tweet"]["rest_id"])
        if "retweet" not in head:
            break
        head = head["retweet"]

    assert head["state"] == "Available"
    assert head["depth"] == MAX_DEPTH
    assert head["tweet"]["rest_id"] == f"900{MAX_DEPTH}"
    # one raw hop remains on the terminal tweet instead of a 6th schema level
    tail = head["tweet"]["legacy"]["retweeted_status_result"]["result"]
    assert tail["rest_id"] == f"900{MAX_DEPTH + 1}"
    assert ids == [f"900{i}" for i in range(MAX_DEPTH + 1)]

    validate(spec, "TweetUnwrapped", unwrapped)
    print(
        f"ok: {MAX_DEPTH + 1}-hop chain stops at depth {MAX_DEPTH}; raw tail "
        "kept on legacy.retweeted_status_result"
    )


def test_self_reference_terminates(spec):
    timeline_tweet = load_sample("06_self_reference_input.json")
    # must return instead of looping forever
    unwrapped, _ = unwrap_tweet(timeline_tweet["tweet_results"], MAX_DEPTH)

    head = unwrapped
    depths = []
    while "retweet" in head:
        depths.append(head["depth"])
        head = head["retweet"]
    assert head["depth"] <= MAX_DEPTH
    assert depths == list(range(head["depth"]))
    validate(spec, "TweetUnwrapped", unwrapped)
    print("ok: self-referential chain terminates (seen-id guard + depth bound)")


def test_tweet_detail_response_validates(spec):
    response = load_sample("07_tweet_detail_response_input.json")
    validate(spec, "TweetDetailResponse", response)
    view = response["data"]["tweet_unwrapped"]
    assert view["tweet"]["rest_id"] == "5001"
    assert view["tweet"]["legacy"]["full_text"] == "focal tweet"
    # the same focal tweet is reachable through the raw timeline structure
    entry = response["data"]["threaded_conversation_with_injections_v2"][
        "instructions"
    ][0]["entries"][0]
    raw = entry["content"]["itemContent"]["tweet_results"]["result"]
    assert raw["rest_id"] == "5001"
    print("ok: full TweetDetailResponse with tweet_unwrapped validates")


def test_tweet_result_by_rest_id_unavailable(spec):
    response = load_sample("08_tweet_result_by_rest_id_input.json")
    validate(spec, "TweetResultByRestIdResponse", response)
    view = response["data"]["tweet_unwrapped"]
    assert view == {
        "state": "Unavailable",
        "__typename": "TweetUnavailable",
        "reason": "Protected",
        "depth": 0,
    }
    print("ok: TweetResultByRestIdResponse carries an unavailable unwrap view")


def test_negative_cases_rejected(spec):
    # Available without a tweet is invalid
    bad_available = {"state": "Available", "depth": 0}
    validator = validator_for(spec, "TweetUnwrapped")
    assert any(
        e.validator in ("required", "oneOf")
        for e in validator.iter_errors(bad_available)
    )

    # An unknown state cannot satisfy either branch
    bad_state = {"state": "Mystery", "__typename": "Tweet"}
    assert any(e.validator == "oneOf" for e in validator.iter_errors(bad_state))

    # depth beyond the enum is rejected
    bad_depth = {
        "state": "Available",
        "depth": 99,
        "tweet": {"rest_id": "1"},
    }
    assert list(validator.iter_errors(bad_depth))
    print("ok: malformed unwrap results are rejected by the schema")


def main():
    if not os.path.isdir(os.path.join(REPO_ROOT, "dist")):
        print("dist/ not found; run `python tools/build.py` first", file=sys.stderr)
        return 1

    failures = 0
    for profile in ("docs", "compatible"):
        print(f"\n=== profile: {profile} ===")
        spec = load_spec(profile)
        for test in (
            test_spec_metaschema,
            test_all_refs_resolve,
            test_tweet_union_unchanged,
            test_referenced_by_tweet_detail,
            test_depth_chain_is_bounded,
            test_quote_is_kept_not_unfolded,
            test_visibility_then_retweet,
            test_tombstone,
            test_unavailable_keeps_reason,
            test_preview_display_keeps_preview,
            test_over_depth_chain,
            test_self_reference_terminates,
            test_tweet_detail_response_validates,
            test_tweet_result_by_rest_id_unavailable,
            test_negative_cases_rejected,
        ):
            try:
                test(spec)
            except Exception:  # noqa: BLE001 - report and continue
                failures += 1
                print(f"FAIL: {test.__name__}")
                import traceback

                traceback.print_exc()

    print()
    if failures:
        print(f"{failures} test(s) failed")
        return 1
    print("all TweetUnwrapped tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
