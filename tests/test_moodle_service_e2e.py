"""
End to end tests for MoodleService.fetch_state against a local fake Moodle endpoint.

A small aiohttp server answers the subset of Moodle web service functions that
moodle-dl uses for forums. This exercises the real RequestHelper retry logic,
the module level failure isolation and the summary returned to the caller.
"""

import asyncio
import json
import threading
from types import SimpleNamespace

import pytest
from aiohttp import web

from moodle_dl.config import ConfigHelper
from moodle_dl.database import StateRecorder
from moodle_dl.moodle.moodle_service import MoodleService
from moodle_dl.moodle.request_helper import InvalidTokenRequestError
from moodle_dl.types import MoodleDlOpts, MoodleURL

REST_PATH = '/webservice/rest/server.php'
COURSE_ID = 42
WORKING_FORUM_CMID = 1001
BROKEN_FORUM_CMID = 1002


def make_opts(path: str) -> MoodleDlOpts:
    return SimpleNamespace(
        path=path,
        max_parallel_api_calls=4,
        max_parallel_downloads=4,
        max_parallel_yt_dlp=1,
        download_chunk_size=1024,
        log_responses=False,
        skip_cert_verify=False,
        allow_insecure_ssl=False,
        use_all_ciphers=False,
    )


class FakeMoodle:
    def __init__(self, broken_forum_mode: str):
        """
        @param broken_forum_mode: 'retry_exhausted' makes the discussions endpoint
                                  of the broken forum answer 429 forever.
                                  'invalidtoken' answers with an expired token error.
        """
        self.broken_forum_mode = broken_forum_mode
        self.port = None
        self.runner = None
        self.loop = None
        self.thread = None
        self.discussion_requests = []

    @staticmethod
    def _course_contents():
        return [
            {
                'id': 1,
                'name': 'Section 1',
                'summary': '',
                'modules': [
                    {
                        'id': WORKING_FORUM_CMID,
                        'name': 'Working forum',
                        'modname': 'forum',
                        'url': 'https://example.com/mod/forum/view.php?id=1001',
                        'description': '',
                        'contents': [],
                    },
                    {
                        'id': BROKEN_FORUM_CMID,
                        'name': 'Broken forum',
                        'modname': 'forum',
                        'url': 'https://example.com/mod/forum/view.php?id=1002',
                        'description': '',
                        'contents': [],
                    },
                ],
            }
        ]

    async def _handle(self, request):
        body = await request.post()
        function = body.get('wsfunction', '')

        if function == 'core_webservice_get_site_info':
            return web.json_response({'userid': 1, 'version': '2024010100.00'})

        if function == 'core_enrol_get_users_courses':
            return web.json_response([{'id': COURSE_ID, 'fullname': 'Test course'}])

        if function == 'core_course_get_contents':
            return web.json_response(self._course_contents())

        if function == 'mod_forum_get_forums_by_courses':
            return web.json_response(
                [
                    {'id': 1, 'course': COURSE_ID, 'cmid': WORKING_FORUM_CMID, 'name': 'Working forum'},
                    {'id': 2, 'course': COURSE_ID, 'cmid': BROKEN_FORUM_CMID, 'name': 'Broken forum'},
                ]
            )

        if function in (
            'mod_forum_get_forum_discussions',
            'mod_forum_get_forum_discussions_paginated',
        ):
            forum_id = int(body.get('forumid', 0))
            self.discussion_requests.append(forum_id)
            if forum_id == 2 and self.broken_forum_mode != 'none':
                if self.broken_forum_mode == 'invalidtoken':
                    return web.json_response(
                        {
                            'exception': 'moodle_exception',
                            'errorcode': 'invalidtoken',
                            'message': 'Invalid token',
                        }
                    )
                return web.json_response({'error': 'too many requests'}, status=429)

            return web.json_response(
                {
                    'discussions': (
                        [
                            {
                                'discussion': 12345,
                                'subject': 'Welcome',
                                'timemodified': 1,
                                'created': 1,
                            }
                        ]
                        if int(body.get('page', 0)) == 0
                        else []
                    )
                }
            )

        if function == 'mod_forum_get_discussion_posts':
            return web.json_response(
                {
                    'posts': [
                        {
                            'id': 9001,
                            'parentid': 0,
                            'author': {'fullname': 'Some one'},
                            'timecreated': 1,
                            'message': 'Hello',
                            'attachments': [],
                        }
                    ]
                }
            )

        # Every other mod endpoint simply returns an empty result.
        return web.json_response({})

    async def __aenter__(self):
        # The server runs in its own thread with its own event loop. The production
        # code performs blocking `requests` calls (core_webservice_get_site_info) which
        # would dead-lock a server that shares the caller's event loop.
        ready = threading.Event()

        def start_server():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self.loop = loop

            async def setup():
                app = web.Application()
                app.router.add_post(REST_PATH, self._handle)
                self.runner = web.AppRunner(app)
                await self.runner.setup()
                site = web.TCPSite(self.runner, '127.0.0.1', 0)
                await site.start()
                self.port = site._server.sockets[0].getsockname()[1]
                ready.set()

            loop.run_until_complete(setup())
            loop.run_forever()

        self.thread = threading.Thread(target=start_server, daemon=True)
        self.thread.start()
        ready.wait(timeout=10)
        assert self.port is not None
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if self.loop is not None:
            close_future = asyncio.run_coroutine_threadsafe(self.runner.cleanup(), self.loop)
            close_future.result(timeout=10)
            self.loop.call_soon_threadsafe(self.loop.stop)
        if self.thread is not None:
            self.thread.join(timeout=10)

    def moodle_url(self) -> MoodleURL:
        return MoodleURL(True, f'127.0.0.1:{self.port}', '/')


def make_config(tmp_path, fake_moodle: FakeMoodle) -> ConfigHelper:
    config = ConfigHelper(make_opts(str(tmp_path)))
    whole_config = {
        'moodle_domain': f'127.0.0.1:{fake_moodle.port}',
        'moodle_path': '/',
        'use_http': True,
        'token': 'a-valid-token',
        'privatetoken': None,
        'userid': 1,
        'version': 2024010100,
        'download_forums': True,
    }
    config._whole_config = whole_config  # pylint: disable=protected-access
    config.config_path = str(tmp_path / 'config.json')
    with open(config.config_path, 'w', encoding='utf-8') as config_file:
        json.dump(whole_config, config_file)
    return config


def run(coroutine):
    return asyncio.run(coroutine)


def collect_files(courses):
    return [(course.id, file.module_id, file.content_filename) for course in courses for file in course.files]


def test_partial_success_keeps_working_module_and_reports_failure(tmp_path):
    """
    The broken forum exhausts its retries, the working forum is merged normally
    and the failure is reported with course and module details.
    """

    async def scenario():
        async with FakeMoodle('retry_exhausted') as fake:
            config = make_config(tmp_path, fake)
            database = StateRecorder(config, make_opts(str(tmp_path)))
            service = MoodleService(config, make_opts(str(tmp_path)))

            changes = await service.fetch_state(database)

            assert service.failed_modules is not None
            assert len(service.failed_modules) == 1
            failure = service.failed_modules.failures[0]
            assert failure.course_id == COURSE_ID
            assert failure.course_name == 'Test course'
            assert failure.module_id == BROKEN_FORUM_CMID
            assert failure.mod_name == 'forum'
            assert failure.module_name == 'Broken forum'
            assert failure.failure_kind == 'retries exhausted'
            assert failure.retry_exhausted is True
            assert 'Too Many Requests' in failure.reason

            summary = service.failed_modules.get_summary()
            assert summary.startswith('1 module(s) failed to fetch')
            assert f'course {COURSE_ID} "Test course"' in summary
            assert 'forum module 1002 "Broken forum"' in summary

            # The working forum still contributes its files.
            files = collect_files(changes)
            working_files = [entry for entry in files if entry[1] == WORKING_FORUM_CMID]
            assert working_files, 'The successfully fetched forum lost all of its files'
            broken_files = [entry for entry in files if entry[1] == BROKEN_FORUM_CMID]
            assert broken_files == []

    run(scenario())


def test_all_modules_successful_has_no_failure_summary(tmp_path):
    """If nothing fails, behaviour matches the old behaviour: no failure summary."""

    async def scenario():
        async with FakeMoodle('none') as fake:
            config = make_config(tmp_path, fake)
            database = StateRecorder(config, make_opts(str(tmp_path)))
            service = MoodleService(config, make_opts(str(tmp_path)))

            changes = await service.fetch_state(database)

            assert service.failed_modules is not None
            assert len(service.failed_modules) == 0
            assert service.failed_modules.get_summary() == ''
            files = collect_files(changes)
            assert any(entry[1] == WORKING_FORUM_CMID for entry in files)
            assert any(entry[1] == BROKEN_FORUM_CMID for entry in files)

    run(scenario())


def test_invalid_token_still_terminates_the_run(tmp_path):
    """An expired token is a global error and must not be downgraded to a module failure."""

    async def scenario():
        async with FakeMoodle('invalidtoken') as fake:
            config = make_config(tmp_path, fake)
            database = StateRecorder(config, make_opts(str(tmp_path)))
            service = MoodleService(config, make_opts(str(tmp_path)))

            with pytest.raises(InvalidTokenRequestError) as error_info:
                await service.fetch_state(database)

            assert 'token has expired' in str(error_info.value)
            assert len(service.failed_modules) == 0

    run(scenario())


def test_invalid_token_is_not_swallowed_by_mod_permission_handlers(tmp_path):
    """
    Modules catch RequestRejectedError for missing access rights. A token error must
    not be swallowed by these handlers even though it is a RequestRejectedError.
    """

    async def scenario():
        from moodle_dl.moodle.mods.quiz import QuizMod

        mod = QuizMod(
            request_helper=SimpleNamespace(),
            moodle_version=2024010100,
            user_id=1,
            last_timestamps={},
            config=SimpleNamespace(),
        )

        async def async_post(function, data=None, timeout=60):
            raise InvalidTokenRequestError('Your Moodle token has expired.')

        mod.client = SimpleNamespace(async_post=async_post)

        with pytest.raises(InvalidTokenRequestError):
            await mod.load_files_of_attempt({'id': 1, 'state': 'finished'})

    run(scenario())


def test_failed_module_is_not_marked_as_synced(tmp_path):
    """
    A stored file of a module that fails to fetch must keep its database state,
    so that it is retried on the next run instead of being considered deleted.
    """

    async def scenario():
        async with FakeMoodle('retry_exhausted') as fake:
            config = make_config(tmp_path, fake)
            opts = make_opts(str(tmp_path))
            database = StateRecorder(config, opts)
            service = MoodleService(config, opts)

            # First run: everything works and the broken forum gets stored.
            async with FakeMoodle('none') as healthy:
                healthy_config = make_config(tmp_path, healthy)
                healthy_database = StateRecorder(healthy_config, opts)
                healthy_service = MoodleService(healthy_config, opts)
                first_changes = await healthy_service.fetch_state(healthy_database)
                for course in first_changes:
                    for file in course.files:
                        if file.deleted or file.content_fileurl is None:
                            continue
                        healthy_database.save_file(file, course.id, course.fullname)

            # Second run: the broken forum exhausts retries.
            changes = await service.fetch_state(database)
            deleted_ids = [file.module_id for course in changes for file in course.files if file.deleted]
            assert BROKEN_FORUM_CMID not in deleted_ids

    run(scenario())
