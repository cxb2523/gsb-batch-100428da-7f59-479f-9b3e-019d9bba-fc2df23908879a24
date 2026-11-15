import json
import sqlite3

import pandas as pd
import pytest

from reddit_multimodal_crawler import crawler as crawler_module
from reddit_multimodal_crawler.crawler import (
    COMMENTS_MANIFEST_FILENAME,
    POSTS_MANIFEST_FILENAME,
    STATUS_FAILURE,
    STATUS_PARTIAL_SUCCESS,
    STATUS_SUCCESS,
    Crawler,
)

POST_COLUMNS = [
    "titles",
    "authors",
    "upvote_ratios",
    "upvotes",
    "created",
    "nsfw",
    "multimedia_url",
    "multimedia_type",
    "extracted_time",
    "body",
    "subreddit",
    "title_polarity_neg",
    "title_polarity_pos",
    "title_compound",
    "body_polarity_neg",
    "body_polarity_pos",
    "body_compound",
    "urls",
]

COMMENT_COLUMNS = [
    "post_title",
    "post_user",
    "comment",
    "comment_user",
    "post_created",
    "subreddit",
    "comment_created",
]


class FakeSIA:
    """Stand-in for nltk's VADER analyzer (the corpus may not be installed)."""

    def polarity_scores(self, text):
        return {"neg": 0.0, "neu": 1.0, "pos": 0.0, "compound": 0.0}


class FakeAuthor:
    def __init__(self, name):
        self.name = name


class FakeComment:
    def __init__(self, id="c1", body="comment body", author="bob", created_utc=1.0):
        self.id = id
        self._body = body
        self.author = FakeAuthor(author) if author is not None else None
        self.created_utc = created_utc

    @property
    def body(self):
        return self._body

    @body.setter
    def body(self, value):
        self._body = value


class FakeComments:
    """Iterable that optionally raises part way through (a broken listing)."""

    def __init__(self, comments, interrupt_after=None, message="listing interrupted"):
        self.comments = list(comments)
        self.interrupt_after = interrupt_after
        self.message = message

    def __iter__(self):
        for index, comment in enumerate(self.comments):
            if self.interrupt_after is not None and index >= self.interrupt_after:
                raise RuntimeError(self.message)
            yield comment


class FakePost:
    def __init__(
        self,
        id="p1",
        title="A title",
        author="alice",
        upvote_ratio=0.9,
        score=10,
        created_utc=1_700_000_000.0,
        over_18=False,
        url="https://example.com/posts/1",
        selftext="post body",
        is_video=False,
        media=None,
        comments=None,
    ):
        self.id = id
        self._title = title
        self.author = FakeAuthor(author) if author is not None else None
        self.upvote_ratio = upvote_ratio
        self._score = score
        self.created_utc = created_utc
        self.over_18 = over_18
        self.url = url
        self.selftext = selftext
        self.is_video = is_video
        self.media = media
        self.comments = [] if comments is None else comments

    @property
    def title(self):
        return self._title

    @title.setter
    def title(self, value):
        self._title = value

    @property
    def score(self):
        return self._score

    @score.setter
    def score(self, value):
        self._score = value


@pytest.fixture
def make_crawler(monkeypatch, tmp_path):
    """Build a Crawler with the sentiment analyzer injected and no network."""

    def factory(base_dir=None, posts_map=None):
        if base_dir is None:
            base_dir = tmp_path / "data"
        instance = Crawler(
            client_id="client-id",
            client_secret="client-secret",
            user_agent="tests/1.0",
            base_dir=str(base_dir),
        )
        # Inject a deterministic analyzer; the real VADER corpus is optional.
        instance.sia = FakeSIA()
        if posts_map is not None:

            def fake_get_posts(self, subreddit_name, sort_by="top", limit=None):
                result = posts_map[subreddit_name]
                if isinstance(result, BaseException):
                    raise result
                return result

            monkeypatch.setattr(Crawler, "_get_posts", fake_get_posts)
        return instance

    return factory


def read_manifest(base_dir, filename):
    with open(base_dir / filename, encoding="utf-8") as handle:
        return json.load(handle)


def test_crawler_constructs_without_vader_corpus(monkeypatch):
    """Construction must not require the optional NLTK corpus."""

    def explode():
        raise AssertionError("VADER must be created lazily")

    monkeypatch.setattr(crawler_module, "SIA", explode)

    crawler = Crawler(
        client_id="client-id",
        client_secret="client-secret",
        user_agent="tests/1.0",
    )

    assert crawler.posts_outcome == []
    assert crawler.comments_outcome == []


# ----------------------------------------------------------------------
# get_posts
# ----------------------------------------------------------------------
def test_get_posts_reads_all_posts_and_records_success(make_crawler, tmp_path):
    posts_map = {
        "python": [
            FakePost(id="p1", title="First", author="alice"),
            FakePost(
                id="p2",
                title="Second",
                author=None,
                url="https://example.com/image.png",
            ),
        ],
        "datascience": [FakePost(id="p3", title="Third", author="carol")],
    }
    crawler = make_crawler(posts_map=posts_map)

    df = crawler.get_posts(["python", "datascience"], save=False)

    assert list(df.columns) == POST_COLUMNS
    assert len(df) == 3
    assert df["titles"].tolist() == ["First", "Second", "Third"]
    assert df["subreddit"].tolist() == ["r/python", "r/python", "r/datascience"]
    assert df["multimedia_type"].tolist() == ["text", "image", "text"]

    assert crawler.posts_outcome == [
        {
            "subreddit": "python",
            "status": STATUS_SUCCESS,
            "collected": 2,
            "failures": [],
        },
        {
            "subreddit": "datascience",
            "status": STATUS_SUCCESS,
            "collected": 1,
            "failures": [],
        },
    ]
    # save=False keeps the disk untouched.
    assert not (tmp_path / "data").exists()


def test_get_posts_skips_broken_post_and_keeps_the_rest(make_crawler):
    class BrokenPost(FakePost):
        @property
        def score(self):
            raise AttributeError("score is unavailable")

    posts_map = {
        "python": [
            FakePost(id="p1", title="Good one"),
            BrokenPost(id="p2", title="Broken one"),
            FakePost(id="p3", title="Good two"),
        ],
        "datascience": [FakePost(id="p4", title="Other subreddit")],
    }
    crawler = make_crawler(posts_map=posts_map)

    df = crawler.get_posts(["python", "datascience"], save=False)

    assert df["titles"].tolist() == ["Good one", "Good two", "Other subreddit"]
    assert list(df.columns) == POST_COLUMNS

    python_outcome, datascience_outcome = crawler.posts_outcome
    assert python_outcome["subreddit"] == "python"
    assert python_outcome["status"] == STATUS_PARTIAL_SUCCESS
    assert python_outcome["collected"] == 2
    assert len(python_outcome["failures"]) == 1
    failure = python_outcome["failures"][0]
    assert failure["post_id"] == "p2"
    assert "score is unavailable" in failure["reason"]
    assert datascience_outcome["status"] == STATUS_SUCCESS
    assert datascience_outcome["collected"] == 1


def test_get_posts_marks_subreddit_failure_and_continues(make_crawler):
    posts_map = {
        "private": RuntimeError("403 forbidden"),
        "python": [FakePost(id="p1", title="Still collected")],
    }
    crawler = make_crawler(posts_map=posts_map)

    df = crawler.get_posts(["private", "python"], save=False)

    assert df["titles"].tolist() == ["Still collected"]

    private_outcome, python_outcome = crawler.posts_outcome
    assert private_outcome["subreddit"] == "private"
    assert private_outcome["status"] == STATUS_FAILURE
    assert private_outcome["collected"] == 0
    assert "403 forbidden" in private_outcome["failures"][0]["reason"]
    assert python_outcome["status"] == STATUS_SUCCESS


def test_get_posts_listing_interrupt_keeps_posts_already_read(make_crawler):
    class InterruptedPosts(list):
        def __iter__(self):
            yield FakePost(id="p1", title="Before break")
            raise RuntimeError("listing broke")

    posts_map = {
        "python": InterruptedPosts(),
        "datascience": [FakePost(id="p2", title="Other subreddit")],
    }
    crawler = make_crawler(posts_map=posts_map)

    df = crawler.get_posts(["python", "datascience"], save=False)

    assert df["titles"].tolist() == ["Before break", "Other subreddit"]
    python_outcome = crawler.posts_outcome["python"]
    assert python_outcome["status"] == STATUS_PARTIAL_SUCCESS
    assert python_outcome["collected"] == 1
    assert "listing broke" in python_outcome["failures"][0]["reason"]
    assert crawler.posts_outcome["datascience"]["status"] == STATUS_SUCCESS


def test_get_posts_outcome_manifest_is_written_beside_data(make_crawler, tmp_path):
    base_dir = tmp_path / "data"
    crawler = make_crawler(base_dir=base_dir, posts_map={"python": [FakePost()]})

    crawler.get_posts(["python"], save=True, save_format="csv")

    manifest_path = base_dir / POSTS_MANIFEST_FILENAME
    assert manifest_path.exists()
    manifest = read_manifest(base_dir, POSTS_MANIFEST_FILENAME)
    assert manifest == [
        {
            "subreddit": "python",
            "status": STATUS_SUCCESS,
            "collected": 1,
            "failures": [],
        }
    ]
    # The manifest must not overwrite the dataset.
    assert (base_dir / "posts.csv").exists()
    assert pd.read_csv(base_dir / "posts.csv").shape[0] == 1


# ----------------------------------------------------------------------
# get_comments
# ----------------------------------------------------------------------
def test_get_comments_reads_all_comments(make_crawler):
    posts_map = {
        "python": [
            FakePost(
                id="p1",
                title="Post one",
                author="alice",
                comments=[FakeComment(id="c1"), FakeComment(id="c2", author=None)],
            ),
            FakePost(
                id="p2",
                title="Post two",
                author=None,
                comments=[FakeComment(id="c3", body="last")],
            ),
        ]
    }
    crawler = make_crawler(posts_map=posts_map)

    df = crawler.get_comments(["python"], save=False)

    assert list(df.columns) == COMMENT_COLUMNS
    assert len(df) == 3
    assert df["comment"].tolist() == ["comment body", "comment body", "last"]
    assert df["post_title"].tolist() == ["Post one", "Post one", "Post two"]
    assert df["subreddit"].tolist() == ["python"] * 3
    assert crawler.comments_outcome == [
        {
            "subreddit": "python",
            "status": STATUS_SUCCESS,
            "collected": 3,
            "failures": [],
        }
    ]


def test_get_comments_respects_limit_per_post(make_crawler):
    posts_map = {
        "python": [
            FakePost(
                id="p1",
                comments=[FakeComment(id="c1"), FakeComment(id="c2")],
            ),
            FakePost(id="p2", comments=[FakeComment(id="c3")]),
        ]
    }
    crawler = make_crawler(posts_map=posts_map)

    df = crawler.get_comments(["python"], limit=1, save=False)

    assert len(df) == 2
    assert df["comment"].tolist() == ["comment body", "comment body"]
    assert crawler.comments_outcome[0]["status"] == STATUS_SUCCESS


def test_get_comments_keeps_read_comments_when_listing_interrupts(make_crawler):
    posts_map = {
        "python": [
            FakePost(
                id="p1",
                title="Interrupted post",
                comments=FakeComments(
                    [FakeComment(id="c1"), FakeComment(id="c2")],
                    interrupt_after=1,
                    message="comment stream closed",
                ),
            ),
            FakePost(
                id="p2",
                title="Healthy post",
                comments=[FakeComment(id="c3")],
            ),
        ]
    }
    crawler = make_crawler(posts_map=posts_map)

    df = crawler.get_comments(["python"], save=False)

    assert df["comment"].tolist() == ["comment body", "comment body"]
    assert df["post_title"].tolist() == ["Interrupted post", "Healthy post"]

    outcome = crawler.comments_outcome[0]
    assert outcome["status"] == STATUS_PARTIAL_SUCCESS
    assert outcome["collected"] == 2
    assert len(outcome["failures"]) == 1
    failure = outcome["failures"][0]
    assert failure["post_id"] == "p1"
    assert failure["collected"] == 1
    assert "comment stream closed" in failure["reason"]


def test_get_comments_skips_unreadable_post_and_continues(make_crawler):
    class BrokenTitlePost(FakePost):
        @property
        def title(self):
            raise AttributeError("title is unavailable")

    class UnreadableComment(FakeComment):
        @property
        def body(self):
            raise RuntimeError("comment body is unavailable")

    posts_map = {
        "python": [
            BrokenTitlePost(id="bad-post"),
            FakePost(
                id="p2",
                title="Post with bad comment",
                comments=[FakeComment(id="c1"), UnreadableComment(id="c2")],
            ),
            FakePost(
                id="p3",
                title="Post three",
                comments=[FakeComment(id="c3")],
            ),
        ]
    }
    crawler = make_crawler(posts_map=posts_map)

    df = crawler.get_comments(["python"], save=False)

    # The comment read before the failure is kept, and the next post is visited.
    assert df["post_title"].tolist() == [
        "Post with bad comment",
        "Post three",
    ]

    outcome = crawler.comments_outcome[0]
    assert outcome["status"] == STATUS_PARTIAL_SUCCESS
    assert outcome["collected"] == 2
    reasons = [failure["reason"] for failure in outcome["failures"]]
    assert any("title is unavailable" in reason for reason in reasons)
    assert any("comment body is unavailable" in reason for reason in reasons)
    comment_failure = next(
        failure for failure in outcome["failures"] if failure.get("comment_id") == "c2"
    )
    assert comment_failure["post_id"] == "p2"
    assert comment_failure["collected"] == 1


def test_get_comments_marks_subreddit_failure_and_continues(make_crawler):
    posts_map = {
        "private": RuntimeError("403 forbidden"),
        "python": [FakePost(comments=[FakeComment()])],
    }
    crawler = make_crawler(posts_map=posts_map)

    df = crawler.get_comments(["private", "python"], save=False)

    assert len(df) == 1
    private_outcome, python_outcome = crawler.comments_outcome
    assert private_outcome["status"] == STATUS_FAILURE
    assert private_outcome["collected"] == 0
    assert "403 forbidden" in private_outcome["failures"][0]["reason"]
    assert python_outcome["status"] == STATUS_SUCCESS


# ----------------------------------------------------------------------
# base_dir handling
# ----------------------------------------------------------------------
def test_nested_base_dir_is_created_recursively(make_crawler, tmp_path):
    base_dir = tmp_path / "one" / "two" / "three"
    crawler = make_crawler(
        base_dir=base_dir,
        posts_map={"python": [FakePost(comments=[FakeComment()])]},
    )

    crawler.get_posts(["python"], save=True, save_format="csv")
    crawler.get_comments(["python"], save=True, save_format="csv")

    assert base_dir.is_dir()
    assert (base_dir / "posts.csv").exists()
    assert (base_dir / "comments.csv").exists()
    assert (base_dir / POSTS_MANIFEST_FILENAME).exists()
    assert (base_dir / COMMENTS_MANIFEST_FILENAME).exists()
    assert read_manifest(base_dir, COMMENTS_MANIFEST_FILENAME)[0]["collected"] == 1


# ----------------------------------------------------------------------
# save_format branches
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "save_format, expected_name",
    [
        ("csv", "posts.csv"),
        ("tsv", "posts.tsv"),
        ("parquet", "posts.parquet"),
        ("json", "posts.json"),
        ("sql", "posts.sql"),
        ("pickle", "posts.pkl"),
    ],
)
def test_posts_save_format_branches(make_crawler, tmp_path, save_format, expected_name):
    base_dir = tmp_path / "out" / "nested"
    crawler = make_crawler(
        base_dir=base_dir,
        posts_map={
            "python": [FakePost(id="p1", title="One"), FakePost(id="p2", title="Two")]
        },
    )

    df = crawler.get_posts(["python"], save=True, save_format=save_format)

    saved_path = base_dir / expected_name
    assert saved_path.exists()

    if save_format == "csv":
        reloaded = pd.read_csv(saved_path)
    elif save_format == "tsv":
        reloaded = pd.read_csv(saved_path, sep="\t")
    elif save_format == "parquet":
        reloaded = pd.read_parquet(saved_path)
    elif save_format == "json":
        reloaded = pd.read_json(saved_path, orient="records")
    elif save_format == "pickle":
        reloaded = pd.read_pickle(saved_path)
    else:
        connection = sqlite3.connect(saved_path)
        try:
            reloaded = pd.read_sql_query("SELECT * FROM posts", connection)
        finally:
            connection.close()

    assert len(reloaded) == len(df) == 2
    assert list(reloaded.columns) == POST_COLUMNS
    assert reloaded["titles"].tolist() == ["One", "Two"]


@pytest.mark.parametrize(
    "save_format, expected_name",
    [
        ("csv", "comments.csv"),
        ("tsv", "comments.tsv"),
        ("parquet", "comments.parquet"),
        ("json", "comments.json"),
        ("sql", "comments.sql"),
        ("pickle", "comments.pkl"),
    ],
)
def test_comments_save_format_branches(
    make_crawler, tmp_path, save_format, expected_name
):
    base_dir = tmp_path / "out"
    crawler = make_crawler(
        base_dir=base_dir,
        posts_map={
            "python": [
                FakePost(title="One", comments=[FakeComment(id="c1")]),
                FakePost(title="Two", comments=[FakeComment(id="c2")]),
            ]
        },
    )

    df = crawler.get_comments(["python"], save=True, save_format=save_format)

    saved_path = base_dir / expected_name
    assert saved_path.exists()

    if save_format == "csv":
        reloaded = pd.read_csv(saved_path)
    elif save_format == "tsv":
        reloaded = pd.read_csv(saved_path, sep="\t")
    elif save_format == "parquet":
        reloaded = pd.read_parquet(saved_path)
    elif save_format == "json":
        reloaded = pd.read_json(saved_path, orient="records")
    elif save_format == "pickle":
        reloaded = pd.read_pickle(saved_path)
    else:
        connection = sqlite3.connect(saved_path)
        try:
            reloaded = pd.read_sql_query("SELECT * FROM comments", connection)
        finally:
            connection.close()

    assert len(reloaded) == len(df) == 2
    assert set(COMMENT_COLUMNS).issubset(set(reloaded.columns))


def test_sql_uses_supplied_connection(make_crawler, tmp_path):
    crawler = make_crawler(posts_map={"python": [FakePost(title="One")]})
    connection = sqlite3.connect(":memory:")
    try:
        crawler.get_posts(
            ["python"], save=True, save_format="sql", sql_connection=connection
        )
        rows = connection.execute("SELECT titles FROM posts").fetchall()
    finally:
        connection.close()

    assert rows == [("One",)]
    assert not (tmp_path / "data" / "posts.sql").exists()


def test_unsupported_save_format_raises(make_crawler, tmp_path):
    crawler = make_crawler(posts_map={"python": [FakePost()]})

    with pytest.raises(NotImplementedError):
        crawler.get_posts(["python"], save=True, save_format="xml")
    with pytest.raises(NotImplementedError):
        crawler.get_comments(["python"], save=True, save_format="xml")

    assert not (tmp_path / "data").exists()


def test_comments_outcome_manifest_does_not_overwrite_data(make_crawler, tmp_path):
    base_dir = tmp_path / "data"
    crawler = make_crawler(
        base_dir=base_dir,
        posts_map={"python": [FakePost(comments=[FakeComment(id="c1")])]},
    )

    crawler.get_comments(["python"], save=True, save_format="json")

    assert (base_dir / "comments.json").exists()
    assert (base_dir / COMMENTS_MANIFEST_FILENAME).exists()
    assert read_manifest(base_dir, COMMENTS_MANIFEST_FILENAME) == [
        {
            "subreddit": "python",
            "status": STATUS_SUCCESS,
            "collected": 1,
            "failures": [],
        }
    ]


def test_get_posts_without_save_writes_nothing(make_crawler, tmp_path):
    crawler = make_crawler(posts_map={"python": [FakePost()]})

    crawler.get_posts(["python"], save=False, save_format="parquet")
    crawler.get_comments(["python"], save=False, save_format="parquet")

    assert not (tmp_path / "data").exists()
