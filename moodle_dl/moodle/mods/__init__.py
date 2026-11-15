import asyncio
from typing import Dict, List, Tuple

from moodle_dl.config import ConfigHelper
from moodle_dl.moodle.mods.common import (
    FailedModuleFetch,
    FailedModules,
    MoodleMod,
    classify_module_failure,
    short_error_text,
)
from moodle_dl.moodle.request_helper import RequestHelper
from moodle_dl.types import Course

from moodle_dl.moodle.mods.assign import AssignMod  # noqa: F401 isort:skip
from moodle_dl.moodle.mods.book import BookMod  # noqa: F401 isort:skip
from moodle_dl.moodle.mods.calendar import CalendarMod  # noqa: F401 isort:skip
from moodle_dl.moodle.mods.data import DataMod  # noqa: F401 isort:skip
from moodle_dl.moodle.mods.folder import FolderMod  # noqa: F401 isort:skip
from moodle_dl.moodle.mods.forum import ForumMod  # noqa: F401 isort:skip
from moodle_dl.moodle.mods.lesson import LessonMod  # noqa: F401 isort:skip
from moodle_dl.moodle.mods.page import PageMod  # noqa: F401 isort:skip
from moodle_dl.moodle.mods.quiz import QuizMod  # noqa: F401 isort:skip
from moodle_dl.moodle.mods.workshop import WorkshopMod  # noqa: F401 isort:skip

ALL_MODS = [Class for name, Class in globals().items() if name.endswith('Mod') and name != 'MoodleMod']


def get_all_mods_classes() -> List[MoodleMod]:
    return ALL_MODS


def get_all_mods(
    request_helper: RequestHelper,
    moodle_version: int,
    user_id: int,
    last_timestamps: Dict[str, Dict[int, int]],
    config: ConfigHelper,
) -> List[MoodleMod]:
    result = []
    for mod in ALL_MODS:
        result.append(mod(request_helper, moodle_version, user_id, last_timestamps, config))
    return result


def _known_modules_of_mod(
    mod_name: str, courses: List[Course], core_contents: Dict[int, List[Dict]]
) -> List[Tuple[int, int, str]]:
    """
    Enumerates the course modules of one mod type from the already loaded course structures.
    @return: List of (course id, course module id, module name)
    """
    known_modules = []
    for course in courses:
        for section in core_contents.get(course.id, []):
            for module in section.get('modules', []):
                if module.get('modname', '') == mod_name:
                    known_modules.append((course.id, module.get('id', 0), module.get('name', '')))
    return known_modules


def _mod_wide_failures(
    mod: MoodleMod,
    error: BaseException,
    failure_kind: str,
    retry_exhausted: bool,
    courses: List[Course],
    core_contents: Dict[int, List[Dict]],
) -> List[FailedModuleFetch]:
    """
    Builds the failure entries for a mod whose listing request failed as a whole.
    The affected modules are taken from the course structures, so that their stored
    database state can be protected as well.
    """
    reason = short_error_text(error)
    known_modules = _known_modules_of_mod(mod.MOD_NAME, courses, core_contents)
    if len(known_modules) == 0:
        # No module of that type is known in the course structures (e.g. calendar).
        # The failure is recorded mod wide, so that all stored files of that mod type
        # are treated as still valid.
        return [
            FailedModuleFetch(
                course_id=0,
                module_id=0,
                mod_name=mod.MOD_NAME,
                module_name='',
                reason=reason,
                failure_kind=failure_kind,
                retry_exhausted=retry_exhausted,
            )
        ]

    return [
        FailedModuleFetch(
            course_id=course_id,
            module_id=module_id,
            mod_name=mod.MOD_NAME,
            module_name=module_name,
            reason=reason,
            failure_kind=failure_kind,
            retry_exhausted=retry_exhausted,
            course_name='',
            mod_wide=True,
        )
        for course_id, module_id, module_name in known_modules
    ]


async def fetch_mods_files(
    mods_to_fetch: List[MoodleMod], courses_to_load: List[Course], core_contents: Dict[int, List[Dict]]
) -> Tuple[Dict[str, Dict], FailedModules]:
    """
    Fetches the files of all mods.

    Failures that are local to a mod do not abort the whole fetch: the successful
    results of the other mods are returned and the failures are collected.
    Global errors (like an expired token) are still raised immediately.

    @return: A tuple of the fetched files, indexed by mod name, then by courses,
             then module id, and the collected module failures.
    """
    tasks = {asyncio.ensure_future(mod.fetch_mod_entries(courses_to_load, core_contents)): mod for mod in mods_to_fetch}
    result = {}
    failed_modules = FailedModules()

    pending = set(tasks)
    while len(pending) > 0:
        done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_EXCEPTION)

        # Retrieve the result of every finished task first, so that no exception
        # gets lost when a global error aborts the run below.
        failures_of_done = {}
        for task in done:
            if task.cancelled():
                continue
            error = task.exception()
            if error is not None:
                failures_of_done[task] = error

        global_error = None
        for task in done:
            mod = tasks[task]
            error = failures_of_done.get(task)
            if error is None:
                if not task.cancelled():
                    result[mod.MOD_NAME] = task.result()
                failed_modules.extend(mod.failed_modules.failures)
                continue

            try:
                failure_kind, retry_exhausted = classify_module_failure(error)
            except BaseException as unhandled_error:  # pylint: disable=broad-exception-caught
                # Global errors (like an expired token) must abort the whole run.
                global_error = unhandled_error
                break

            # The whole listing of this mod type failed. Keep the mod known to the
            # result builder, but without any files, and record the affected modules.
            result[mod.MOD_NAME] = {}
            failed_modules.extend(mod.failed_modules.failures)
            failed_modules.extend(
                _mod_wide_failures(mod, error, failure_kind, retry_exhausted, courses_to_load, core_contents)
            )

        if global_error is not None:
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            raise global_error

    failed_modules.refresh_course_names(courses_to_load)
    return result, failed_modules


def get_mod_plurals():
    result = {}
    for mod in ALL_MODS:
        result[mod.MOD_NAME] = mod.MOD_PLURAL_NAME
    return result
