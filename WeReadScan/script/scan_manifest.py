'''
scan_manifest.py
Copyright 2020 by Algebra-FUN
ALL RIGHTS RESERVED.

Durable on-disk manifest helpers for resumable book scanning.

Every successfully captured page is written to the manifest and flushed
to disk immediately, so that an interruption (page load timeout, missing
button, page structure change, ...) never invalidates the pages captured
before it. A later run can load the manifest and reuse the screenshots
already stored under ``wrs-temp/<book>/context``.
'''

import json
import os


MANIFEST_FILE_NAME = 'manifest.json'


def get_manifest_path(book_temp_dir):
    """return the manifest file path inside a book temp directory."""
    return f'{book_temp_dir}/{MANIFEST_FILE_NAME}'


def load_manifest(manifest_path):
    """
    load manifest records from disk.

    :Returns:
        a list of records, each one being
        ``{'chapter': str, 'page': int, 'image': str}``.
        An empty list is returned when no (valid) manifest exists yet.
    """
    if not os.path.exists(manifest_path):
        return []
    try:
        with open(manifest_path, 'r', encoding='utf-8') as f:
            records = json.load(f)
    except (OSError, ValueError):
        return []
    if not isinstance(records, list):
        return []
    return records


def _write_manifest(manifest_path, records):
    with open(manifest_path, 'w', encoding='utf-8') as f:
        json.dump(records, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())


def upsert_manifest(manifest_path, records, chapter, page, image):
    """
    record one captured page into the manifest and flush it to disk now.

    A page is identified by its ``(chapter, page)`` pair: when the same
    page is recorded again (e.g. a resumed run re-capturing a page whose
    old screenshot file was lost), the existing record is replaced in
    place instead of being duplicated. The manifest order always follows
    the order pages were first captured, which is the chapter / page
    reading order required for PDF merging.

    :Args:
     - manifest_path: manifest json file path
     - records: in-memory manifest list to update
     - chapter: chapter name of the captured page
     - page: page number inside the chapter
     - image: path (without ``.png`` suffix) of the captured screenshot

    :Returns:
        the mutated records list (also persisted to ``manifest_path``)
    """
    for record in records:
        if record.get('chapter') == chapter and record.get('page') == page:
            record['image'] = image
            break
    else:
        records.append({
            'chapter': chapter,
            'page': page,
            'image': image,
        })
    _write_manifest(manifest_path, records)
    return records
