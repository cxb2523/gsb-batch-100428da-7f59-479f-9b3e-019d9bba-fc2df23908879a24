from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

import bcrypt
import numpy as np
import pandas as pd
import praw
from nltk.sentiment.vader import SentimentIntensityAnalyzer as SIA

#: Status values used in the per-subreddit outcome manifests.
STATUS_SUCCESS = "success"
STATUS_PARTIAL_SUCCESS = "partial_success"
STATUS_FAILURE = "failure"

#: Formats that can be handed to :meth:`Crawler.get_posts` / :meth:`Crawler.get_comments`.
SUPPORTED_SAVE_FORMATS = ("csv", "tsv", "parquet", "json", "sql", "pickle")

#: File names of the outcome manifests.  They intentionally do not collide with
#: the ``posts.*`` / ``comments.*`` data files.
POSTS_MANIFEST_FILENAME = "posts_outcome.json"
COMMENTS_MANIFEST_FILENAME = "comments_outcome.json"

#: Default file names for each ``save_format``.  ``sql`` is a SQLite database
#: holding a table named after the dataset kind (``posts`` / ``comments``); it
#: can be queried with ``sqlite3.connect(path)`` or any SQLAlchemy engine.
DATA_FILENAMES = {
    "posts": {
        "csv": "posts.csv",
        "tsv": "posts.tsv",
        "parquet": "posts.parquet",
        "json": "posts.json",
        "sql": "posts.sql",
        "pickle": "posts.pkl",
    },
    "comments": {
        "csv": "comments.csv",
        "tsv": "comments.tsv",
        "parquet": "comments.parquet",
        "json": "comments.json",
        "sql": "comments.sql",
        "pickle": "comments.pkl",
    },
}

_NOT_IMPLEMENTED_MESSAGE = """The format, you want the code is not implemented. But, you can extend the Crawler.get_posts() method by maneuvering the return object accordingly.

Also, you may want to try entering the format in lower case!
"""


def _json_default(value: Any) -> Any:
    """Make values such as bcrypt byte hashes JSON serialisable."""
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8", "backslashreplace")
    if isinstance(value, np.generic):
        return value.item()
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _format_exception(exc: BaseException) -> str:
    message = str(exc).strip()
    return f"{type(exc).__name__}: {message}" if message else type(exc).__name__


class SubredditOutcomeList(list):
    """List of per-subreddit outcome records.

    Behaves like a plain list (and serialises as a JSON array), but also allows
    looking a record up by subreddit name::

        outcome["python"]["status"]
    """

    def _find(self, subreddit_name: str) -> Dict[str, Any]:
        for entry in self:
            if entry.get("subreddit") == subreddit_name:
                return entry
        raise KeyError(subreddit_name)

    def __getitem__(self, key):
        if isinstance(key, str):
            return self._find(key)
        return super().__getitem__(key)

    def get(self, subreddit_name: str, default=None):
        try:
            return self._find(subreddit_name)
        except KeyError:
            return default

    def keys(self) -> List[str]:
        return [entry["subreddit"] for entry in self]


class Crawler(object):
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        user_agent: str,
        base_dir: str = "./data/",
    ) -> None:
        """Initializes the object of Crawler, which will eventually scrape the data from Reddit.

        Args:
            * client_id (str) : client_id is an id given by Reddit API.
            * client_secret (str) : client_secret is the secret key given by Reddit API when registering the project.
            * user_agent (str) : this is kind of a discription of the project
                - This is usually written as `<APP_NAME> <VERSION> /u/<USERNAME>`.
            * base_dir (str) : directory the datasets and outcome manifests are written to.
                It is created recursively when missing.
        """
        self.client_id = client_id
        self.client_secret = client_secret
        self.user_agent = user_agent
        self.reddit = praw.Reddit(
            client_id=self.client_id,
            client_secret=self.client_secret,
            user_agent=self.user_agent,
        )
        self.base_dir = base_dir
        self._sia = None

        #: Per-subreddit outcome of the most recent ``get_posts`` call.
        self.posts_outcome: SubredditOutcomeList = SubredditOutcomeList()
        #: Per-subreddit outcome of the most recent ``get_comments`` call.
        self.comments_outcome: SubredditOutcomeList = SubredditOutcomeList()

    @property
    def sia(self):
        """Lazily created VADER analyzer (also assignable, which aids testing)."""
        if self._sia is None:
            self._sia = SIA()
        return self._sia

    @sia.setter
    def sia(self, analyzer) -> None:
        self._sia = analyzer

    def _get_posts(
        self, subreddit_name: str, sort_by: str = "top", limit: int | None = None
    ):
        """This method of Crawler, retrieves the posts of a particular subreddit ordered by the sort_by parameter.

        Args:
            * subreddit_name (str) : The subreddit you want to crawl through.
            * sort_by (str) : The preference of tags to find.
            * limit (int) : The maximum number of posts you want.

        Return:
            * Returns a `praw.models.listing.generator.ListingGenerator` object.
                - The data can be accessed by using a simple for loop.
        """
        if sort_by not in ["hot", "new", "rising", "top"]:
            raise NotImplementedError(
                'This wrapper is only implemented for ["hot", "new", "rising", "top"].'
            )
        interface = self.reddit.subreddit(subreddit_name)
        iterator = getattr(interface, sort_by)
        posts = iterator(limit=limit)
        return posts

    # ------------------------------------------------------------------
    # Outcome / persistence helpers
    # ------------------------------------------------------------------
    def _validate_save_format(self, save_format: str) -> None:
        if save_format not in SUPPORTED_SAVE_FORMATS:
            raise NotImplementedError(_NOT_IMPLEMENTED_MESSAGE)

    def _ensure_base_dir(self) -> Path:
        """Create ``base_dir`` recursively (including nested missing parents)."""
        base_dir = self.base_dir
        path = Path(base_dir) if base_dir not in (None, "") else Path(".")
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _save_dataframe(
        self,
        df: pd.DataFrame,
        kind: str,
        save_format: str,
        sql_connection: Any | None = None,
    ) -> Path:
        """Persist ``df`` with the pandas writer matching ``save_format``.

        Args:
            df: the dataframe to persist.
            kind: ``"posts"`` or ``"comments"``; used for file/table names.
            save_format: one of :data:`SUPPORTED_SAVE_FORMATS`.
            sql_connection: optional DBAPI connection or SQLAlchemy engine used
                for ``save_format="sql"``.  When omitted a SQLite database is
                created inside ``base_dir``.

        Returns:
            The path of the written file (``base_dir`` for SQL connections).
        """
        base_dir = self._ensure_base_dir()
        path = base_dir / DATA_FILENAMES[kind][save_format]

        if save_format == "csv":
            df.to_csv(path, index=False)
        elif save_format == "tsv":
            df.to_csv(path, sep="\t", index=False)
        elif save_format == "parquet":
            df.to_parquet(path, index=False)
        elif save_format == "json":
            df.to_json(
                path,
                orient="records",
                indent=2,
                force_ascii=False,
                date_format="iso",
                default_handler=_json_default,
            )
        elif save_format == "sql":
            if sql_connection is None:
                connection = sqlite3.connect(path)
                try:
                    df.to_sql(kind, connection, if_exists="replace", index=False)
                    connection.commit()
                finally:
                    connection.close()
            else:
                df.to_sql(kind, sql_connection, if_exists="replace", index=False)
                commit = getattr(sql_connection, "commit", None)
                if callable(commit):
                    commit()
        elif save_format == "pickle":
            df.to_pickle(path)
        else:  # pragma: no cover - guarded by _validate_save_format
            raise NotImplementedError(_NOT_IMPLEMENTED_MESSAGE)

        return path

    def _save_outcome(
        self, outcome: "SubredditOutcomeList", manifest_filename: str
    ) -> Path:
        base_dir = self._ensure_base_dir()
        path = base_dir / manifest_filename
        with open(path, "w", encoding="utf-8") as manifest_file:
            json.dump(outcome, manifest_file, ensure_ascii=False, indent=2, default=str)
        return path

    @staticmethod
    def _outcome_entry(
        subreddit_name: str,
        status: str,
        collected: int,
        failures: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        return {
            "subreddit": subreddit_name,
            "status": status,
            "collected": collected,
            "failures": failures,
        }

    @staticmethod
    def _failure_entry(
        post_id: str | None,
        exc: BaseException,
        comment_id: str | None = None,
        collected_comments: int | None = None,
    ) -> Dict[str, Any]:
        failure: Dict[str, Any] = {
            "post_id": post_id,
            "reason": _format_exception(exc),
        }
        if comment_id is not None:
            failure["comment_id"] = comment_id
        if collected_comments is not None:
            failure["collected"] = collected_comments
        return failure

    @staticmethod
    def _post_identifier(post: Any, fallback: str) -> str:
        for attribute in ("id", "name", "permalink", "url", "title"):
            try:
                value = getattr(post, attribute)
            except Exception:
                continue
            if value is not None:
                return str(value)
        return fallback

    @staticmethod
    def _comment_identifier(comment: Any) -> str | None:
        try:
            value = getattr(comment, "id", None)
        except Exception:
            return None
        return None if value is None else str(value)

    def _build_post_row(self, post: Any, subreddit_name: str) -> Dict[str, Any]:
        """Read every field of a single post.

        The row is fully materialised before it is appended to the column
        buffers, so a failing attribute access cannot leave a half-written row.
        """
        row: Dict[str, Any] = {}

        row["titles"] = post.title

        if post.author is not None:
            row["authors"] = bcrypt.hashpw(
                post.author.name.encode("utf8"), bcrypt.gensalt()
            )
        else:
            row["authors"] = np.nan

        row["upvote_ratios"] = post.upvote_ratio
        row["upvotes"] = post.score
        row["created"] = post.created_utc
        row["nsfw"] = post.over_18
        row["urls"] = post.url
        row["body"] = post.selftext
        row["subreddit"] = f"r/{subreddit_name}"
        row["extracted_time"] = datetime.now()

        if post.is_video:
            row["multimedia_type"] = "video"
            row["multimedia_url"] = post.media["reddit_video"]["fallback_url"]
        else:
            url = post.url
            if url.split(".")[-1] in ["png", "jpg", "jpeg"]:
                row["multimedia_url"] = url
                row["multimedia_type"] = "image"
            else:
                row["multimedia_url"] = np.nan
                row["multimedia_type"] = "text"

        title_polarity = self.sia.polarity_scores(post.title)
        body_polarity = self.sia.polarity_scores(post.selftext)

        row["title_polarity_neg"] = title_polarity["neg"]
        row["title_polarity_pos"] = title_polarity["pos"]
        row["title_compound"] = title_polarity["compound"]
        row["body_polarity_neg"] = body_polarity["neg"]
        row["body_polarity_pos"] = body_polarity["pos"]
        row["body_compound"] = body_polarity["compound"]

        return row

    @staticmethod
    def _comment_row(
        post_title: Any,
        comment: Any,
        post_user: Any,
        post_created: Any,
        subreddit_name: str,
    ) -> Dict[str, Any]:
        return {
            "post_title": post_title,
            "post_user": post_user,
            "comment": comment.body,
            "comment_user": (
                comment.author.name if comment.author is not None else np.nan
            ),
            "post_created": post_created,
            "subreddit": subreddit_name,
            "comment_created": comment.created_utc,
        }

    # ------------------------------------------------------------------
    # Public crawlers
    # ------------------------------------------------------------------
    def get_posts(
        self,
        subreddit_names: List[str],
        sort_by: str = "top",
        limit: int | None = None,
        save: bool = True,
        save_format: str = "csv",
        sql_connection: Any | None = None,
    ) -> pd.DataFrame:
        """This method will help us extract the posts from a list of subreddits. This method saves the dataframe in different formats based on the request.

        Note: The name of the user will be encoded.

        Args:
            - subreddit_names (List[str]): This parameter expects a list which contains the names of the subreddit you want to scrape through.
            - sort_by (str): This parameter is a query to get the relevant posts.
            - limit (int | None): This is to control the number of parameters you want. None by default.
            - save (bool): This to say if you want to save the dataset or not. True by default.
            - save_format (str): This is to specify the format in which youn want to save the data. "csv" by default. The characters are expected to be lower case.
                Supported values: csv, tsv, parquet, json, sql, pickle.
            - sql_connection: Optional DBAPI connection / SQLAlchemy engine used for
                ``save_format="sql"``.  Defaults to a SQLite database in ``base_dir``.

        Returns:
            Although this method will save the data in which ever format, it will return to you a `pd.DataFrame` object.

            The per-subreddit outcome is available in ``self.posts_outcome`` and,
            when ``save`` is true, written to ``posts_outcome.json`` inside
            ``base_dir``.
        """

        if save:
            self._validate_save_format(save_format)

        data: Dict[str, List[Any]] = {
            "titles": [],
            "authors": [],
            "upvote_ratios": [],
            "upvotes": [],
            "created": [],
            "nsfw": [],
            "multimedia_url": [],
            "multimedia_type": [],
            "extracted_time": [],
            "body": [],
            "subreddit": [],
            "title_polarity_neg": [],
            "title_polarity_pos": [],
            "title_compound": [],
            "body_polarity_neg": [],
            "body_polarity_pos": [],
            "body_compound": [],
            "urls": [],
        }

        self.posts_outcome = SubredditOutcomeList()

        for subreddit_name in subreddit_names:
            failures: List[Dict[str, Any]] = []
            collected = 0
            status = STATUS_SUCCESS

            try:
                posts = self._get_posts(
                    subreddit_name=subreddit_name, sort_by=sort_by, limit=limit
                )
                iterator = iter(posts)
            except NotImplementedError:
                raise
            except Exception as exc:
                # The whole subreddit could not be fetched; keep going with the
                # remaining ones.
                self.posts_outcome.append(
                    self._outcome_entry(
                        subreddit_name,
                        STATUS_FAILURE,
                        0,
                        [self._failure_entry(None, exc)],
                    )
                )
                continue

            post_index = 0
            while True:
                try:
                    post = next(iterator)
                except StopIteration:
                    break
                except Exception as exc:
                    # Fetching the listing failed midway: keep the posts already
                    # read and move on to the next subreddit.
                    status = STATUS_PARTIAL_SUCCESS if collected else STATUS_FAILURE
                    failures.append(self._failure_entry(None, exc))
                    break

                try:
                    row = self._build_post_row(post, subreddit_name)
                except Exception as exc:
                    # One broken post must not discard the rest of the batch.
                    status = STATUS_PARTIAL_SUCCESS
                    failures.append(
                        self._failure_entry(
                            self._post_identifier(
                                post, f"{subreddit_name}:post#{post_index}"
                            ),
                            exc,
                        )
                    )
                    post_index += 1
                    continue

                values = {key: row[key] for key in data}
                for key, value in values.items():
                    data[key].append(value)
                collected += 1
                post_index += 1

            self.posts_outcome.append(
                self._outcome_entry(subreddit_name, status, collected, failures)
            )

        print({k: len(v) for k, v in data.items()})

        df = pd.DataFrame(data)

        if save:
            self._save_dataframe(df, "posts", save_format, sql_connection)
            self._save_outcome(self.posts_outcome, POSTS_MANIFEST_FILENAME)

        return df

    def get_comments(
        self,
        subreddit_names: List[str],
        sort_by: str = "top",
        limit: int | None = None,
        save: bool = True,
        save_format: str = "csv",
        sql_connection: Any | None = None,
    ) -> pd.DataFrame:
        """This method will return the comments for each post.

        Note: The name of the user will be encoded.

        Args:
            - subreddit_names (List[str]): This parameter expects a list which contains the names of the subreddit you want to scrape through.
            - sort_by (str): This parameter is a query to get the relevant posts.
            - limit (int | None): This is to control the number of parameters you want. None by default.
            - save (bool): This to say if you want to save the dataset or not. True by default.
            - save_format (str): This is to specify the format in which youn want to save the data. "csv" by default. The characters are expected to be lower case.
                Supported values: csv, tsv, parquet, json, sql, pickle.
            - sql_connection: Optional DBAPI connection / SQLAlchemy engine used for
                ``save_format="sql"``.  Defaults to a SQLite database in ``base_dir``.

        Returns:
            Although this method will save the data in which ever format, it will return to you a `pd.DataFrame` object.

            The per-subreddit outcome is available in ``self.comments_outcome``
            and, when ``save`` is true, written to ``comments_outcome.json``
            inside ``base_dir``.
        """

        if save:
            self._validate_save_format(save_format)

        data: Dict[str, List[Any]] = {
            "post_title": [],
            "post_user": [],
            "comment": [],
            "comment_user": [],
            "post_created": [],
            "subreddit": [],
            "comment_created": [],
        }

        self.comments_outcome = SubredditOutcomeList()

        for subreddit_name in subreddit_names:
            failures: List[Dict[str, Any]] = []
            collected = 0
            status = STATUS_SUCCESS

            try:
                posts = self._get_posts(
                    subreddit_name=subreddit_name, sort_by=sort_by, limit=limit
                )
                iterator = iter(posts)
            except NotImplementedError:
                raise
            except Exception as exc:
                self.comments_outcome.append(
                    self._outcome_entry(
                        subreddit_name,
                        STATUS_FAILURE,
                        0,
                        [self._failure_entry(None, exc)],
                    )
                )
                continue

            post_index = 0
            while True:
                try:
                    post = next(iterator)
                except StopIteration:
                    break
                except Exception as exc:
                    # The listing broke midway: keep the comments already read.
                    status = STATUS_PARTIAL_SUCCESS if collected else STATUS_FAILURE
                    failures.append(self._failure_entry(None, exc))
                    break

                post_id = self._post_identifier(
                    post, f"{subreddit_name}:post#{post_index}"
                )
                post_index += 1

                try:
                    post_user = post.author.name if post.author is not None else np.nan
                    post_title = post.title
                    post_created = post.created_utc
                    comments_iter = iter(post.comments)
                except Exception as exc:
                    # The post itself could not be read; skip it but keep going.
                    status = STATUS_PARTIAL_SUCCESS
                    failures.append(
                        self._failure_entry(post_id, exc, collected_comments=0)
                    )
                    continue

                comments_collected = 0
                while limit is None or comments_collected < limit:
                    try:
                        comment = next(comments_iter)
                    except StopIteration:
                        break
                    except Exception as exc:
                        # The comment listing broke; keep what was already read.
                        status = STATUS_PARTIAL_SUCCESS
                        failures.append(
                            self._failure_entry(
                                post_id, exc, collected_comments=comments_collected
                            )
                        )
                        break

                    try:
                        row = self._comment_row(
                            post_title,
                            comment,
                            post_user,
                            post_created,
                            subreddit_name,
                        )
                    except Exception as exc:
                        # Keep the comments read so far for this post, record the
                        # interruption and move on to the next post.
                        status = STATUS_PARTIAL_SUCCESS
                        failures.append(
                            self._failure_entry(
                                post_id,
                                exc,
                                comment_id=self._comment_identifier(comment),
                                collected_comments=comments_collected,
                            )
                        )
                        break

                    values = {key: row[key] for key in data}
                    for key, value in values.items():
                        data[key].append(value)
                    collected += 1
                    comments_collected += 1

            self.comments_outcome.append(
                self._outcome_entry(subreddit_name, status, collected, failures)
            )

        df = pd.DataFrame(data)

        if save:
            self._save_dataframe(df, "comments", save_format, sql_connection)
            self._save_outcome(self.comments_outcome, COMMENTS_MANIFEST_FILENAME)

        return df
