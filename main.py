import argparse
import logging
import os
import re
import sys
from contextlib import nullcontext
from dataclasses import dataclass, field
from mimetypes import guess_extension

from dotenv import load_dotenv

# `sync` looks unused, but importing it switches Telethon to its synchronous
# (non-async) API, which this script relies on. Don't remove it.
from telethon import TelegramClient, sync  # noqa: F401
from telethon.tl.types import MessageMediaPhoto

# Load environment variables from the .env file
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Supported file categories
# Note: These are manually curated based on common use cases.
# Feel free to add or remove extensions as needed.
FILE_CATEGORIES = {
    "images": [
        "jpg",
        "jpeg",
        "png",
        "gif",
        "bmp",
        "webp",
        "svg",
        "heic",
        "raw",
    ],
    "documents": [
        "pdf",
        "doc",
        "docx",
        "odt",
        "rtf",
        "xls",
        "xlsx",
        "csv",
        "ppt",
        "pptx",
        "txt",
        "epub",
    ],
    "videos": [
        "mp4",
        "mkv",
        "avi",
        "mov",
        "wmv",
        "webm",
    ],
    "audios": [
        "mp3",
        "wav",
        "aac",
        "flac",
        "ogg",
        "opus",
        "m4a",
    ],
    "archives": [
        "zip",
        "rar",
        "7z",
        "tar",
        "gz",
        "bz2",
        "xz",
    ],
}


@dataclass(frozen=True)
class DownloadedFile:
    """A file that was downloaded during this run."""

    message_id: int | None
    file_path: str
    size: int


@dataclass(frozen=True)
class SkippedFile:
    """A target that already existed and was therefore left untouched."""

    message_id: int | None
    file_path: str
    size: int


@dataclass(frozen=True)
class FailedFile:
    """A message whose media could not be downloaded, with a readable reason."""

    message_id: int | None
    file_path: str
    reason: str


@dataclass
class DownloadSummary:
    """Structured result of a download run, used for the final report."""

    new_files: list[DownloadedFile] = field(default_factory=list)
    skipped_files: list[SkippedFile] = field(default_factory=list)
    failed_files: list[FailedFile] = field(default_factory=list)
    removed_temp_files: list[str] = field(default_factory=list)

    @property
    def new_count(self) -> int:
        return len(self.new_files)

    @property
    def skipped_count(self) -> int:
        return len(self.skipped_files)

    @property
    def failed_count(self) -> int:
        return len(self.failed_files)

    @property
    def new_size_bytes(self) -> int:
        return sum(file.size for file in self.new_files)

    @property
    def skipped_size_bytes(self) -> int:
        return sum(file.size for file in self.skipped_files)

    @property
    def total_size_bytes(self) -> int:
        # Files already on disk are still part of what this run accounts for,
        # and mirror what the output directory now contains.
        return self.new_size_bytes + self.skipped_size_bytes

    @property
    def new_size_mb(self) -> float:
        return self.new_size_bytes / (1024 * 1024)

    @property
    def skipped_size_mb(self) -> float:
        return self.skipped_size_bytes / (1024 * 1024)

    @property
    def total_size_mb(self) -> float:
        return self.total_size_bytes / (1024 * 1024)


@dataclass(frozen=True)
class DownloadOutcome:
    """Result of attempting a single file: new, skipped, or failed."""

    status: str
    file_path: str
    size: int = 0
    reason: str = ""


def sanitize_filename(filename: str, fallback: str = "unnamed_file") -> str:
    """
    Remove or replace characters that are invalid across major operating systems
    (Windows, macOS, Linux), preventing filesystem write errors and path traversal.
    """
    if not filename:
        return str(fallback)
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", filename)
    cleaned = cleaned.rstrip(". ")
    if not cleaned or not cleaned.strip("_."):
        return str(fallback)
    return cleaned


def create_telegram_client():
    """Create the Telegram client, or exit with a clear message if credentials are missing."""
    api_id = os.getenv("TELEGRAM_API_ID")
    api_hash = os.getenv("TELEGRAM_API_HASH")

    if not api_id or not api_hash:
        sys.exit(
            "Error: TELEGRAM_API_ID and TELEGRAM_API_HASH are not set. "
            "See 'Setting Up the .env File' in README.md."
        )
    if not api_id.isdigit():
        sys.exit("Error: TELEGRAM_API_ID must be a number. Check your .env file.")

    # The session file stores your login, so you only need to sign in once.
    return TelegramClient("session_name", int(api_id), api_hash)


def cleanup_incomplete_files(output_dir):
    """Remove any leftover .tmp files from interrupted downloads.

    Returns the paths that were removed so the final summary can mention them.
    """
    removed = []
    for file in sorted(os.listdir(output_dir)):
        if not file.endswith(".tmp"):
            continue
        temp_file_path = os.path.join(output_dir, file)
        if not os.path.isfile(temp_file_path):
            continue
        logger.warning("Removing incomplete file: %s", temp_file_path)
        try:
            os.remove(temp_file_path)
        except OSError as error:
            logger.error("Could not remove %s: %s", temp_file_path, error)
            continue
        removed.append(temp_file_path)
    return removed


def create_directory_if_needed(directory):
    """Creates the directory if it does not exist."""
    if not os.path.exists(directory):
        os.makedirs(directory)


def _message_id(message, fallback=None):
    """Return the Telegram message ID, or a stable fallback for fake clients."""
    message_id = getattr(message, "id", None)
    return fallback if message_id is None else message_id


def _format_error(error: Exception) -> str:
    message = str(error).strip()
    return f"{type(error).__name__}: {message}" if message else type(error).__name__


def _remove_file_if_present(path):
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError as error:
        logger.warning("Could not remove temporary file %s: %s", path, error)


def download_file(telegram_client, message, file_path) -> DownloadOutcome:
    """Download one message's media to ``file_path`` using a .tmp file.

    The returned outcome distinguishes a genuinely new download from a file
    that already existed. Failures never leave a .tmp file behind and always
    carry a human-readable reason.
    """
    if os.path.exists(file_path):
        return DownloadOutcome("skipped", file_path, os.path.getsize(file_path))

    temp_file_path = file_path + ".tmp"
    try:
        downloaded_file = telegram_client.download_media(message, file=temp_file_path)
    except Exception as error:  # noqa: BLE001
        _remove_file_if_present(temp_file_path)
        reason = f"download failed: {_format_error(error)}"
        return DownloadOutcome("failed", file_path, reason=reason)

    completed_path = temp_file_path
    if not os.path.exists(completed_path):
        # The caller asked for the .tmp path, so anything written elsewhere is
        # not ours to move around (and might be an unrelated user file).
        reason = (
            "download completed but produced no file "
            f"(download_media returned {downloaded_file!r})"
        )
        return DownloadOutcome("failed", file_path, reason=reason)

    size = os.path.getsize(completed_path)
    if size <= 0:
        _remove_file_if_present(completed_path)
        reason = "download produced an empty file (0 bytes)"
        return DownloadOutcome("failed", file_path, reason=reason)

    # Re-check: never overwrite a file that appeared while we were downloading.
    if os.path.exists(file_path):
        _remove_file_if_present(completed_path)
        return DownloadOutcome("skipped", file_path, os.path.getsize(file_path))

    try:
        os.rename(completed_path, file_path)
    except OSError as error:
        _remove_file_if_present(completed_path)
        reason = f"could not move temporary file into place: {_format_error(error)}"
        return DownloadOutcome("failed", file_path, reason=reason)

    logger.info("Downloaded: %s", file_path)
    return DownloadOutcome("new", file_path, os.path.getsize(file_path))


def check_and_download_file(telegram_client, message, file_path):
    """Download a single file, returning ``(file_path, size)``.

    Kept for callers that used the original helper directly; the download
    loop uses :func:`download_file` so it can tell new files from skipped
    ones. Returns ``(None, 0)`` when the download fails.
    """
    outcome = download_file(telegram_client, message, file_path)
    if outcome.status == "failed":
        return None, 0
    return outcome.file_path, outcome.size


def _file_type_matches(file_extension, file_type):
    """Apply the original --format filtering rules to a detected extension."""
    if not file_type:
        return True
    normalized_type = file_type.lower()
    extension = file_extension.lstrip(".")
    return (
        normalized_type in FILE_CATEGORIES
        and extension in FILE_CATEGORIES.get(normalized_type, [])
    ) or extension == normalized_type


def _collect_file_candidates(messages, file_type):
    """Extract downloadable message/media pairs and their cleaned base names.

    Returns a list of ``(message, message_id, base_filename)``. Filtering and
    name sanitization behave exactly as before; collision handling happens
    afterwards.
    """
    candidates = []
    for index, message in enumerate(messages):
        if not getattr(message, "media", None):
            continue

        message_id = _message_id(message, fallback=index + 1)

        if isinstance(message.media, MessageMediaPhoto):
            if file_type and file_type.lower() != "images":
                continue
            base_name = sanitize_filename(f"{message_id}.jpg", fallback=str(message_id))
            candidates.append((message, message_id, base_name))
            continue

        file = getattr(message, "file", None)
        if not file:
            continue

        mime_type = getattr(file, "mime_type", None)
        file_extension = guess_extension(mime_type) if mime_type else None
        if file_extension is None:
            file_extension = ""

        raw_name = getattr(file, "name", None) or str(message_id)
        base_name = sanitize_filename(raw_name, fallback=str(message_id))
        if file_extension and not base_name.endswith(file_extension):
            base_name += file_extension

        if not _file_type_matches(file_extension, file_type):
            continue

        candidates.append((message, message_id, base_name))
    return candidates


def _append_disambiguator(filename, marker, attempt):
    """Append a stable marker before the extension.

    The first attempt yields ``stem (marker).ext``; later attempts yield
    ``stem (marker-2).ext`` etc., so a suffix never looks like a filename
    another message genuinely has.
    """
    stem, extension = os.path.splitext(filename)
    suffix = f"({marker})" if attempt == 1 else f"({marker}-{attempt})"
    return f"{stem} {suffix}{extension}"


def assign_unique_file_names(candidates):
    """Attach a stable, collision-free filename to each candidate.

    When two messages in the same batch clean to the same filename, every
    member of that group gets its message ID appended (for example
    ``report (101).pdf`` and ``report (102).pdf``). The result depends only on
    the batch's base names and message IDs, so rerunning the same batch yields
    the same names, and a sibling can never be mistaken for an existing file.

    A generated name may still collide with another message's literal filename
    (a document genuinely called ``report (101).pdf``). In that case the
    literally-named file keeps its name and the generated one is bumped, which
    is stable because message IDs are unique within a channel.

    Returns a list of ``(message, message_id, final_filename)``.
    """
    base_names = [base_name for _, _, base_name in candidates]

    normalized_groups = {}
    for index, base_name in enumerate(base_names):
        normalized_groups.setdefault(base_name.casefold(), []).append(index)

    # Any name shared inside the batch is disambiguated for every member,
    # rather than letting the first one keep an ambiguous base name.
    attempts = [
        1 if len(normalized_groups[base_name.casefold()]) > 1 else 0
        for base_name in base_names
    ]

    # Message IDs identify files stably. If a client ever reports the same ID
    # twice, fall back to the position so the suffix loop still converges.
    seen_markers = {}
    for _, message_id, _ in candidates:
        seen_markers[str(message_id)] = seen_markers.get(str(message_id), 0) + 1
    markers = []
    for position, (_, message_id, _) in enumerate(candidates, start=1):
        marker = str(message_id)
        if seen_markers[marker] > 1:
            marker = f"{marker}-{position}"
        markers.append(marker)

    names = [
        _append_disambiguator(base_name, markers[index], attempts[index])
        if attempts[index]
        else base_name
        for index, base_name in enumerate(base_names)
    ]

    # Resolve remaining collisions deterministically. Names that were already
    # disambiguated give way first, so a literal filename is preserved when
    # possible. The round bound is only a guard against pathological input.
    for _ in range(len(base_names) + 1):
        collisions = {}
        for index, name in enumerate(names):
            collisions.setdefault(name.casefold(), []).append(index)
        duplicate_groups = [
            indices for indices in collisions.values() if len(indices) > 1
        ]
        if not duplicate_groups:
            break
        for indices in duplicate_groups:
            bumped = [index for index in indices if attempts[index] > 0] or indices
            for index in bumped:
                attempts[index] += 1
                names[index] = _append_disambiguator(
                    base_names[index], markers[index], attempts[index]
                )

    return [
        (message, message_id, names[index])
        for index, (message, message_id, _) in enumerate(candidates)
    ]


def _client_session(telegram_client):
    """Allow simple fake clients that are not context managers."""
    if hasattr(telegram_client, "__enter__") and hasattr(telegram_client, "__exit__"):
        return telegram_client
    return nullcontext(telegram_client)


def _log_summary(summary):
    """Log a final report that reconciles every inspected message."""
    if summary.removed_temp_files:
        logger.warning(
            "Removed %d leftover .tmp file(s) from a previous run.",
            len(summary.removed_temp_files),
        )

    for skipped in summary.skipped_files:
        logger.info(
            "Skipped (already exists): message %s -> %s",
            skipped.message_id,
            skipped.file_path,
        )

    for failed in summary.failed_files:
        logger.error(
            "Failed: message %s -> %s: %s",
            failed.message_id,
            failed.file_path,
            failed.reason,
        )

    logger.info(
        "Summary: new downloads: %d (%.2f MB), skipped (already existed): %d "
        "(%.2f MB), failed: %d, total size: %.2f MB",
        summary.new_count,
        summary.new_size_mb,
        summary.skipped_count,
        summary.skipped_size_mb,
        summary.failed_count,
        summary.total_size_mb,
    )


def list_dialogs(telegram_client):
    """List all accessible dialogs (channels, groups, chats) with their details."""
    with telegram_client:
        logger.info("Fetching your dialogs...")
        print(f"\n{'ID':<15} {'Type':<25} {'Name':<30} {'Username':<20}")
        print("-" * 90)
        for dialog in telegram_client.iter_dialogs():
            dialog_id = dialog.id
            dialog_type = type(dialog.entity).__name__
            dialog_name = dialog.name or "(No name)"
            dialog_username = getattr(dialog.entity, "username", "") or "(No username)"

            # Check if it's a channel and whether it's public or private
            if dialog_type == "Channel":
                if hasattr(dialog.entity, "username") and dialog.entity.username:
                    dialog_type = "Channel (public)"
                else:
                    dialog_type = "Channel (private)"

            print(
                f"{dialog_id:<15} {dialog_type:<25} {dialog_name:<30} {dialog_username:<20}"
            )


def resolve_entity(entity_identifier):
    """
    Convert entity identifier to proper format for Telethon.
    Converts numeric strings to integers, keeps usernames and keywords as strings.

    Args:
        entity_identifier: Channel name, username, or numeric ID as string

    Returns:
        Integer for numeric IDs, string for usernames/keywords
    """
    try:
        return int(entity_identifier)
    except (ValueError, TypeError):
        # Keep as string for usernames like '@channel' or 'me'
        return entity_identifier


def download_files_from_entity(
    telegram_client,
    entity_identifier,
    file_type=None,
    save_directory=".",
    message_limit=100,
):
    """Download media from an entity, returning a structured summary.

    ``message_limit`` still limits how many messages are inspected, not how
    many files are downloaded.
    """
    create_directory_if_needed(save_directory)
    summary = DownloadSummary(
        removed_temp_files=cleanup_incomplete_files(save_directory)
    )

    message_limit = None if message_limit == 0 else message_limit

    # Resolve the entity (convert to int if numeric, keep as string otherwise)
    entity = resolve_entity(entity_identifier)

    with _client_session(telegram_client):
        logger.info("Fetching messages from: %s", entity_identifier)
        messages = telegram_client.iter_messages(entity, limit=message_limit)

        # Names are resolved for the whole batch before downloading, so that two
        # messages sharing a filename never race each other or masquerade as
        # "existing". Only media-bearing messages are retained, not the raw
        # message stream, so --limit 0 stays memory-reasonable.
        candidates = _collect_file_candidates(messages, file_type)
        targets = assign_unique_file_names(candidates)

        for message, message_id, file_name in targets:
            file_path = os.path.join(save_directory, file_name)
            outcome = download_file(telegram_client, message, file_path)

            if outcome.status == "new":
                summary.new_files.append(
                    DownloadedFile(message_id, outcome.file_path, outcome.size)
                )
            elif outcome.status == "skipped":
                summary.skipped_files.append(
                    SkippedFile(message_id, outcome.file_path, outcome.size)
                )
            else:
                summary.failed_files.append(
                    FailedFile(message_id, outcome.file_path, outcome.reason)
                )

    _log_summary(summary)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Download files from a Telegram entity (channel, group, chat, or user)."
    )
    parser.add_argument(
        "entity",
        nargs="?",
        type=str,
        help="The username or ID of the Telegram entity (channel, group, chat, or 'me' for saved messages).",
    )
    parser.add_argument(
        "-f",
        "--format",
        type=str,
        help=f"Filter by file type category or specific extension. "
        f"Categories: {', '.join(FILE_CATEGORIES.keys())}. "
        f"Or use specific extensions (e.g., pdf, jpg, mp4). "
        f"If omitted, all media types are downloaded.",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=str,
        default=".",
        help="The directory to save downloaded files. Defaults to the current directory.",
    )
    parser.add_argument(
        "-l",
        "--limit",
        type=int,
        default=100,
        help="Maximum number of messages to check (not files to download). Use 0 for no limit. Defaults to 100.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List all accessible dialogs (channels, groups, chats) and exit.",
    )

    args = parser.parse_args()

    if not args.list and not args.entity:
        parser.error(
            "Please specify an entity or use --list to view available dialogs (channels, groups, chats)."
        )

    # Created after parsing arguments, so `--help` works without credentials.
    telegram_client = create_telegram_client()

    try:
        if args.list:
            list_dialogs(telegram_client)
        else:
            download_files_from_entity(
                telegram_client, args.entity, args.format, args.output, args.limit
            )
    # Show a one-line message instead of a long traceback.
    except Exception as error:  # noqa: BLE001
        logger.error(f"An error occurred: {error}")
