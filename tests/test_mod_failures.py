"""
Tests for the module level failure handling of MoodleMod.

The module loading functions are replaced by injected fakes, so the tests do
not need a Moodle server or a real database.
"""

import asyncio
from types import SimpleNamespace

import pytest

from moodle_dl.database import StateRecorder
from moodle_dl.moodle.mods import fetch_mods_files
from moodle_dl.moodle.mods.common import FailedModuleFetch, FailedModules, MoodleMod
from moodle_dl.moodle.request_helper import (
    InvalidTokenRequestError,
    RequestRejectedError,
    RequestRetriesExhaustedError,
)
from moodle_dl.types import MOD_WIDE_FAILURE_COURSE_ID, Course, File, MoodleDlOpts


class FakeMod(MoodleMod):
    """A tiny mod with a fixed list of entries, used to inject load functions."""

    MOD_NAME = 'fake'
    MOD_PLURAL_NAME = 'fakes'
    MOD_MIN_VERSION = 0

    @classmethod
    def download_condition(cls, config, file):
        return True

    def __init__(self, entries):
        super().__init__(
            request_helper=SimpleNamespace(),
            moodle_version=2024010100,
            user_id=1,
            last_timestamps={},
            config=SimpleNamespace(),
        )
        self.entries = entries

    async def real_fetch_mod_entries(self, courses, core_contents):
        await self.run_async_load_function_on_mod_entries(self.entries, self.load_entry)
        return self.entries

    async def load_entry(self, entry):
        raise NotImplementedError  # replaced in tests


def make_entries():
    return {
        11: {
            111: {'id': 1, 'name': 'first', 'files': []},
            112: {'id': 2, 'name': 'second', 'files': []},
        },
        12: {
            121: {'id': 3, 'name': 'third', 'files': []},
        },
    }


def make_core_contents():
    return {
        11: [
            {
                'id': 1,
                'name': 'Section 1',
                'modules': [
                    {'id': 111, 'name': 'first', 'modname': 'fake'},
                    {'id': 112, 'name': 'second', 'modname': 'fake'},
                ],
            }
        ],
        12: [
            {
                'id': 2,
                'name': 'Section 1',
                'modules': [{'id': 121, 'name': 'third', 'modname': 'fake'}],
            }
        ],
    }


def make_courses():
    return [Course(11, 'Course 11'), Course(12, 'Course 12')]


def run(coroutine):
    return asyncio.run(coroutine)


def test_partial_failure_keeps_successful_modules():
    """A module whose retries are exhausted must not abort the other modules."""

    async def scenario():
        mod = FakeMod(make_entries())

        async def load_entry(entry):
            if entry['id'] == 2:
                raise RequestRetriesExhaustedError('Connection error: 429 too many requests')
            entry['files'].append({'filename': entry['name'], 'filepath': '/', 'type': 'file'})

        mod.load_entry = load_entry

        fetched, failures = await fetch_mods_files([mod], make_courses(), make_core_contents())

        assert fetched['fake'][11][111]['files'] == [{'filename': 'first', 'filepath': '/', 'type': 'file'}]
        assert 112 not in fetched['fake'][11]
        assert fetched['fake'][12][121]['files'] == [{'filename': 'third', 'filepath': '/', 'type': 'file'}]

        assert len(failures) == 1
        failure = failures.failures[0]
        assert failure.course_id == 11
        assert failure.module_id == 112
        assert failure.mod_name == 'fake'
        assert failure.module_name == 'second'
        assert failure.failure_kind == 'retries exhausted'
        assert failure.retry_exhausted is True
        assert failure.course_name == 'Course 11'

    run(scenario())


def test_rejected_module_is_recorded_as_non_retryable():
    async def scenario():
        mod = FakeMod(make_entries())

        async def load_entry(entry):
            if entry['id'] == 1:
                raise RequestRejectedError('The Moodle System rejected the Request.')
            entry['files'].append({'filename': entry['name'], 'filepath': '/', 'type': 'file'})

        mod.load_entry = load_entry

        fetched, failures = await fetch_mods_files([mod], make_courses(), make_core_contents())

        assert 111 not in fetched['fake'][11]
        assert fetched['fake'][11][112]['files']
        assert len(failures) == 1
        failure = failures.failures[0]
        assert failure.module_id == 111
        assert failure.failure_kind == 'rejected'
        assert failure.retry_exhausted is False

    run(scenario())


def test_all_modules_successful_means_no_failures():
    async def scenario():
        mod = FakeMod(make_entries())

        async def load_entry(entry):
            entry['files'].append({'filename': entry['name'], 'filepath': '/', 'type': 'file'})

        mod.load_entry = load_entry

        fetched, failures = await fetch_mods_files([mod], make_courses(), make_core_contents())

        assert len(failures) == 0
        assert failures.get_summary() == ''
        for course_id, modules in fetched['fake'].items():
            for module_id, module in modules.items():
                assert module['files'], f'Module {course_id}/{module_id} lost its files'

    run(scenario())


def test_whole_mod_listing_failure_is_recorded_per_known_module():
    """
    If the listing request of a mod type fails, all modules of that type that are
    known from the course structures are reported as failed and the other mods
    still deliver their results.
    """

    class WorkingMod(FakeMod):
        MOD_NAME = 'working'
        MOD_PLURAL_NAME = 'workings'

    class BrokenMod(FakeMod):
        MOD_NAME = 'fake'
        MOD_PLURAL_NAME = 'fakes'

        async def real_fetch_mod_entries(self, courses, core_contents):
            raise RequestRetriesExhaustedError('Connection error: 429 too many requests')

    working = WorkingMod({11: {111: {'id': 1, 'name': 'first', 'files': []}}})

    async def load_entry(entry):
        entry['files'].append({'filename': 'ok', 'filepath': '/', 'type': 'file'})

    working.load_entry = load_entry
    broken = BrokenMod({})

    core_contents = make_core_contents()
    core_contents[11][0]['modules'].append({'id': 111, 'name': 'first', 'modname': 'working'})
    courses = make_courses()

    async def scenario():
        fetched, failures = await fetch_mods_files([working, broken], courses, core_contents)

        assert fetched['working'][11][111]['files'] == [{'filename': 'ok', 'filepath': '/', 'type': 'file'}]
        assert fetched['fake'] == {}
        assert len(failures) == 3
        assert {failure.module_id for failure in failures} == {111, 112, 121}
        assert all(failure.failure_kind == 'retries exhausted' for failure in failures)
        assert all(failure.course_name for failure in failures)

    run(scenario())


def test_whole_mod_listing_failure_without_known_modules_is_mod_wide():
    """
    Calendar has no per-course module ids. A whole-listing failure must still protect
    its stored files, so it is recorded mod wide.
    """

    class BrokenMod(FakeMod):
        MOD_NAME = 'calendar'
        MOD_PLURAL_NAME = 'events'

        async def real_fetch_mod_entries(self, courses, core_contents):
            raise RequestRejectedError('The Moodle System rejected the Request.')

    async def scenario():
        fetched, failures = await fetch_mods_files([BrokenMod({})], make_courses(), make_core_contents())
        assert fetched['calendar'] == {}
        assert len(failures) == 1
        failure = failures.failures[0]
        assert failure.module_id == 0
        assert (MOD_WIDE_FAILURE_COURSE_ID, 'calendar') in failures.get_ignored_module_identifiers()

    run(scenario())


def test_invalid_token_aborts_everything():
    """An expired token is a global error and must still terminate the run."""

    async def scenario():
        mod = FakeMod(make_entries())

        async def load_entry(entry):
            # One module fails locally first, so that the run has to survive it.
            if entry['id'] == 1:
                raise RequestRetriesExhaustedError('Connection error')
            raise InvalidTokenRequestError('Your Moodle token has expired.')

        mod.load_entry = load_entry

        with pytest.raises(InvalidTokenRequestError) as error_info:
            await fetch_mods_files([mod], make_courses(), make_core_contents())
        assert 'token has expired' in str(error_info.value)

    run(scenario())


def test_unexpected_error_is_not_swallowed_silently_as_token_error():
    """Non module errors must surface; they are not silently converted."""

    async def scenario():
        mod = FakeMod(make_entries())

        async def load_entry(entry):
            if entry['id'] == 2:
                raise RequestRejectedError('The Moodle System rejected the Request.')
            entry['files'].append({'filename': 'x', 'filepath': '/', 'type': 'file'})

        mod.load_entry = load_entry
        fetched, failures = await fetch_mods_files([mod], make_courses(), make_core_contents())
        assert len(failures) == 1
        assert fetched['fake'][11][111]['files']

    run(scenario())


def test_failure_summary_is_readable():
    failures = FailedModules()
    failures.add(
        FailedModuleFetch(
            course_id=11,
            module_id=112,
            mod_name='forum',
            module_name='Second forum',
            reason='Connection error: 429',
            failure_kind='retries exhausted',
            retry_exhausted=True,
            course_name='Course 11',
        )
    )
    summary = failures.get_summary()
    assert summary.startswith('1 module(s) failed to fetch')
    assert 'course 11 "Course 11"' in summary
    assert 'forum module 112 "Second forum"' in summary
    assert 'retries exhausted' in summary


def test_failed_module_does_not_touch_stored_database_state(tmp_path):
    """
    The stored files of a failed module must not be marked as deleted.
    """
    opts = MoodleDlOpts.__new__(MoodleDlOpts)
    opts.path = str(tmp_path)
    config = SimpleNamespace(get_misc_files_path=lambda: str(tmp_path))
    database = StateRecorder.__new__(StateRecorder)
    database.opts = opts
    database.db_file = str(tmp_path / 'state.db')

    # Create the real database schema.
    StateRecorder.__init__(database, config, opts)

    def make_file(module_id, name):
        return File(
            module_id=module_id,
            section_name='Section 1',
            section_id=1,
            module_name=name,
            content_filepath='/',
            content_filename=name,
            content_fileurl=f'https://example.com/{module_id}/{name}',
            content_filesize=10 + module_id,
            content_timemodified=module_id,
            module_modname='fake',
            content_type='file',
            content_isexternalfile=False,
            saved_to='/tmp/' + name,
        )

    stored_course = Course(11, 'Course 11')
    stored_course.files = [make_file(111, 'first'), make_file(112, 'second')]
    for stored_file in stored_course.files:
        database.save_file(stored_file, stored_course.id, stored_course.fullname)

    # The failed module 112 has no files in the current state.
    current_course = Course(11, 'Course 11')
    current_course.files = [make_file(111, 'first')]

    failures = FailedModules()
    failures.add(
        FailedModuleFetch(
            course_id=11,
            module_id=112,
            mod_name='fake',
            module_name='second',
            reason='Connection error',
            failure_kind='retries exhausted',
            retry_exhausted=True,
        )
    )

    changes = database.changes_of_new_version([current_course], failures)

    deleted_module_ids = [file.module_id for course in changes for file in course.files if file.deleted]
    assert deleted_module_ids == []
    # Without the failure the stored file would have been reported as deleted.
    database_failures = StateRecorder.changes_of_new_version(database, [current_course])
    deleted_without_failure = [file.module_id for course in database_failures for file in course.files if file.deleted]
    assert deleted_without_failure == [112]


def test_whole_mod_type_failure_protects_all_stored_files_of_that_type(tmp_path):
    """
    If the listing of a mod type failed, none of its stored files may be treated as
    deleted, even when the course module ids are not part of the failure entry.
    """
    opts = MoodleDlOpts.__new__(MoodleDlOpts)
    opts.path = str(tmp_path)
    config = SimpleNamespace(get_misc_files_path=lambda: str(tmp_path))
    database = StateRecorder(config, opts)

    def make_file(module_id, name, mod_name='assign'):
        return File(
            module_id=module_id,
            section_name='Section 1',
            section_id=1,
            module_name=name,
            content_filepath='/',
            content_filename=name,
            content_fileurl=f'https://example.com/{module_id}/{name}',
            content_filesize=10 + module_id,
            content_timemodified=module_id,
            module_modname=mod_name,
            content_type='file',
            content_isexternalfile=False,
            saved_to='/tmp/' + name,
        )

    stored_course = Course(11, 'Course 11')
    stored_course.files = [make_file(111, 'first'), make_file(112, 'second')]
    for stored_file in stored_course.files:
        database.save_file(stored_file, stored_course.id, stored_course.fullname)

    # No assign module is part of the current (partially fetched) state.
    current_course = Course(11, 'Course 11')
    current_course.files = []

    failures = FailedModules()
    failures.add(
        FailedModuleFetch(
            course_id=0,
            module_id=0,
            mod_name='assign',
            module_name='',
            reason='Connection error',
            failure_kind='retries exhausted',
            retry_exhausted=True,
        )
    )

    changes = database.changes_of_new_version([current_course], failures)
    assert [file.module_id for course in changes for file in course.files if file.deleted] == []

    # Without the failure both stored assign files are reported as deleted.
    changes_without = database.changes_of_new_version([current_course])
    assert sorted(file.module_id for course in changes_without for file in course.files if file.deleted) == [
        111,
        112,
    ]
