import asyncio
import logging
import math
from abc import ABCMeta, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

from moodle_dl.config import ConfigHelper
from moodle_dl.moodle.request_helper import (
    InvalidTokenRequestError,
    RequestHelper,
    RequestRejectedError,
    RequestRetriesExhaustedError,
    RetryableRequestError,
    is_invalid_token_error,
)
from moodle_dl.types import MOD_WIDE_FAILURE_COURSE_ID, Course, File
from moodle_dl.utils import PathTools as PT
from moodle_dl.utils import get_nested, run_with_final_message


@dataclass
class FailedModuleFetch:
    """
    Describes a single course module whose content could not be fetched completely.

    A module is only marked as failed if the error is local to that module.
    Global errors like an invalid token are never recorded here, they abort the
    whole run instead.
    """

    course_id: int
    module_id: int
    mod_name: str
    module_name: str
    reason: str
    failure_kind: str
    retry_exhausted: bool = False
    course_name: str = ''
    # Id of the concrete mod instance, which can differ from the course module id.
    # It is kept so that already stored files of this module can be protected
    # against being treated as deleted.
    instance_id: int = 0
    # True if the whole listing request of the mod type failed. Then every module
    # of that mod type is affected, not only the one described by module_id.
    mod_wide: bool = False

    def get_ignored_identifiers(self) -> List[Tuple[int, int]]:
        """
        @return: Pairs of (course id, module id) that identify this failed module.
                 Both the course module id and the mod instance id are returned because
                 files may reference either of them.
                 A module id of 0 (or a whole-mod-type failure) means that no single
                 module id is known, so the special MOD_WIDE_FAILURE_COURSE_ID marker
                 is used instead.
        """
        if self.module_id == 0 or self.mod_wide:
            return [(MOD_WIDE_FAILURE_COURSE_ID, self.mod_name)]
        identifiers = [(self.course_id, self.module_id)]
        if self.instance_id != 0 and self.instance_id != self.module_id:
            identifiers.append((self.course_id, self.instance_id))
        return identifiers

    def __str__(self) -> str:
        if self.module_id == 0:
            return f'{self.mod_name} modules (all courses): {self.failure_kind} ({self.reason})'
        course = f'course {self.course_id}'
        if self.course_name != '':
            course += f' "{self.course_name}"'
        module = f'{self.mod_name} module {self.module_id}'
        if self.module_name != '':
            module += f' "{self.module_name}"'
        return f'{course}, {module}: {self.failure_kind} ({self.reason})'


@dataclass
class FailedModules:
    """Aggregated list of all modules that failed during a fetch run."""

    failures: List[FailedModuleFetch] = field(default_factory=list)

    def add(self, failure: FailedModuleFetch):
        # Do not record the very same module twice (e.g. when a mod wide failure
        # repeats a failure that already happened for one of its modules).
        key = (failure.course_id, failure.module_id, failure.mod_name)
        for existing in self.failures:
            if (existing.course_id, existing.module_id, existing.mod_name) == key:
                # A whole-mod-type failure is the stronger statement, so it wins.
                if failure.mod_wide:
                    existing.mod_wide = True
                return
        self.failures.append(failure)

    def extend(self, failures: List[FailedModuleFetch]):
        for failure in failures:
            self.add(failure)

    def __len__(self) -> int:
        return len(self.failures)

    def __iter__(self):
        return iter(self.failures)

    def __bool__(self) -> bool:
        return len(self.failures) > 0

    def refresh_course_names(self, courses: List[Course]):
        "Fills in the human readable course names for all recorded failures."
        names_of_courses = {course.id: course.fullname for course in courses}
        for failure in self.failures:
            if failure.course_id in names_of_courses:
                failure.course_name = names_of_courses[failure.course_id]

    def get_ignored_module_identifiers(self) -> set:
        """
        @return: Set of (course id, module id) pairs of all failed modules.
                 Used to keep the database state of these modules untouched.
        """
        identifiers = set()
        for failure in self.failures:
            identifiers.update(failure.get_ignored_identifiers())
        return identifiers

    def summary_lines(self) -> List[str]:
        "Readable lines describing every failed module."
        return [str(failure) for failure in self.failures]

    def get_summary(self) -> str:
        """
        @return: A multi line summary of all failed modules, or an empty string if
                 no module failed.
        """
        if len(self.failures) == 0:
            return ''
        lines = [f'{len(self.failures)} module(s) failed to fetch:']
        for line in self.summary_lines():
            lines.append(f'  - {line}')
        return '\n'.join(lines)


def _is_global_error(error: BaseException) -> bool:
    """
    Global errors affect every request of the run and must never be swallowed as a
    failure of a single module.
    """
    if isinstance(error, InvalidTokenRequestError):
        return True
    # Be defensive: a plain RequestRejectedError carrying the token expiry message
    # is still a global error.
    if isinstance(error, RequestRejectedError) and is_invalid_token_error(error):
        return True
    return False


def classify_module_failure(error: BaseException) -> Tuple[str, bool]:
    """
    Classifies an exception that occurred while fetching one module.

    The returned failure kind distinguishes an exhausted retry loop
    ('retries exhausted') from a final, non-retryable rejection ('rejected',
    'connection error').

    Global errors (like an expired token) and errors that are not normal module
    errors are re-raised.

    @return: A tuple of a human readable failure kind and whether all retries
             of a retryable request were used up.
    """
    if _is_global_error(error):
        raise error
    if isinstance(error, (RequestRetriesExhaustedError, RetryableRequestError)):
        return 'retries exhausted', True
    if isinstance(error, RequestRejectedError):
        return 'rejected', False
    if isinstance(error, ConnectionError):
        return 'connection error', False
    if isinstance(error, Exception):
        # An unexpected error of one module must not abort the whole run either.
        logging.debug('Unexpected error while fetching a module', exc_info=error)
        return 'unexpected error', False
    # BaseException like CancelledError or KeyboardInterrupt are never module failures.
    raise error


def short_error_text(error: BaseException, max_length: int = 400) -> str:
    "A single line error description, short enough to be displayed in a summary."
    text = ' '.join(str(error).split())
    if text == '':
        text = repr(error)
    if len(text) > max_length:
        text = text[: max_length - 3] + '...'
    return text


class MoodleMod(metaclass=ABCMeta):
    """
    Common class for a Moodle module endpoint
    """

    MOD_NAME = None
    MOD_PLURAL_NAME = None
    MOD_MIN_VERSION = None

    def __init__(
        self,
        request_helper: RequestHelper,
        moodle_version: int,
        user_id: int,
        last_timestamps: Dict[str, Dict[int, int]],
        config: ConfigHelper,
    ):
        """
        @param last_timestamps: A Dict per mod of timestamps per course module id,
                                prevents downloading older content of a corse module
        """

        self.client = request_helper
        self.version = moodle_version
        self.user_id = user_id
        self.last_timestamps = last_timestamps
        self.config = config

        # Modules that failed to be fetched completely during this run.
        self.failed_modules = FailedModules()

    @classmethod
    @abstractmethod
    def download_condition(cls, config: ConfigHelper, file: File) -> bool:
        """
        Return True if moodle-dl is configured to downloaded the given file
        This condition is applied after comparing the current status with the local database
        """
        # TODO: Make module download conditions more granular and more generally
        # (do not only filter "deleted" mod files but all?)
        pass

    async def fetch_mod_entries(
        self, courses: List[Course], core_contents: Dict[int, List[Dict]]
    ) -> Dict[int, Dict[int, Dict]]:
        if self.version < self.MOD_MIN_VERSION:
            return {}

        result = await self.real_fetch_mod_entries(courses, core_contents)
        if len(self.failed_modules) > 0:
            logging.info(
                'Loaded all %s except %d module(s) that failed to fetch',
                self.MOD_PLURAL_NAME,
                len(self.failed_modules),
            )
        else:
            logging.info('Loaded all %s', self.MOD_PLURAL_NAME)
        return result

    def get_indexed_ids_of_mod_instances(self, mod_instances: Dict[int, Dict[int, Dict]]):
        """
        @param mod_instances: Dictionary of all mod instanced, indexed by courses, then module id
        """
        mod_instances_ids = {}
        idx = 0
        for _, modules in mod_instances.items():
            for _, mod in modules.items():
                mod_instances_ids[str(idx)] = mod['id']
                idx += 1
        return mod_instances_ids

    def get_data_for_mod_entries_endpoint(self, courses: List[Course]):
        # Create a dictionary with all the courses we want to request
        course_ids = {}
        for idx, course in enumerate(courses):
            course_ids[str(idx)] = course.id
        return {'courseids': course_ids}

    @abstractmethod
    async def real_fetch_mod_entries(
        self, courses: List[Course], core_contents: Dict[int, List[Dict]]
    ) -> Dict[int, Dict[int, Dict]]:
        """
        Fetches the mod entries for all courses
        @return: Dictionary of all course modules of that mod type, indexed by course id, then course module id
        """
        pass

    @staticmethod
    def set_props_of_file(file_dict: Dict, **props):
        for prop, value in props.items():
            if (
                file_dict.get(prop) is not None
                and file_dict[prop] != ''
                and file_dict[prop] != value
                and (prop != 'filepath' or file_dict[prop] != '/')
            ):
                logging.debug('Overwriting %r with %r of file %s', prop, value, file_dict)
            file_dict[prop] = value

    @classmethod
    def set_props_of_files(cls, files: List[Dict], **props):
        for file_dict in files:
            cls.set_props_of_file(file_dict, **props)

    @staticmethod
    def set_base_file_path_of_file(file_dict: Dict, base_file_path: str):
        old_file_path = file_dict.get('filepath')
        if old_file_path is not None and old_file_path != '/':
            file_dict['filepath'] = PT.make_path(base_file_path, old_file_path.strip('/'))
        else:
            file_dict['filepath'] = base_file_path

    @classmethod
    def set_base_file_path_of_files(cls, files: List[Dict], base_file_path: str):
        for file_dict in files:
            cls.set_base_file_path_of_file(file_dict, base_file_path)

    async def run_async_load_function_on_mod_entries(self, entries: Dict[int, Dict[int, Dict]], load_function):
        """
        Runs a load function on every module in a given entries list.

        If the loading of a single module ultimately fails, only that module is dropped:
        the failure is recorded in self.failed_modules, the other modules keep their
        successfully loaded content and the course is still merged normally.

        Global errors (like an expired token) are re-raised and abort the whole run.

        @param entries: Dictionary of all module entries, indexed by courses, then module id
        """
        ctr = 0
        total_entries = 0
        for _, entries_in_course in entries.items():
            total_entries += len(entries_in_course)

        if total_entries == 0:
            return
        ctr_digits = int(math.log10(total_entries)) + 1

        tasks = {}
        for course_id, entries_in_course in entries.items():
            for module_id, entry in entries_in_course.items():
                ctr += 1

                # Example: [ 5/16] Loaded assign 123 in course 456 "Assignment name"
                loaded_message = (
                    f'[%(ctr){ctr_digits}d/%(total){ctr_digits}d] Loaded %(mod_name)s %(module_id)d'
                    + ' in course %(course_id)d "%(module_name)s"'
                )

                task = asyncio.ensure_future(
                    run_with_final_message(
                        load_function,
                        entry,
                        loaded_message,
                        {
                            'ctr': ctr,
                            'total': total_entries,
                            'mod_name': self.MOD_NAME,
                            'module_id': module_id,
                            'course_id': course_id,
                            'module_name': entry.get('name', ''),
                        },
                    )
                )
                tasks[task] = (course_id, module_id, entry)

        failed_module_ids = await self._wait_for_mod_loads(tasks)

        # Drop the failed modules, so that only completely loaded modules are merged
        # into the course.
        for course_id, module_id in failed_module_ids:
            entries_in_course = entries.get(course_id, {})
            entries_in_course.pop(module_id, None)
            if len(entries_in_course) == 0:
                entries.pop(course_id, None)

    async def _wait_for_mod_loads(self, tasks: Dict) -> List[Tuple[int, int]]:
        """
        Waits for all module load tasks and records the failed modules.
        A failure of one module does not abort the loading of the other modules.
        @return: List of (course id, module id) pairs of modules that failed.
        """
        failed_module_ids = []
        pending = set(tasks)
        while len(pending) > 0:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_EXCEPTION)

            # Retrieve the result of every finished task first, so that no exception
            # gets lost when a global error aborts the run below.
            exceptions_of_done = {}
            for task in done:
                if task.cancelled():
                    continue
                exception = task.exception()
                if exception is not None:
                    exceptions_of_done[task] = exception

            global_error = None
            for task in done:
                error = exceptions_of_done.get(task)
                if error is None:
                    continue
                try:
                    failure_kind, retry_exhausted = classify_module_failure(error)
                except BaseException as unhandled_error:  # pylint: disable=broad-exception-caught
                    # A global error (like an expired token) must abort the whole run.
                    global_error = unhandled_error
                    break

                course_id, module_id, entry = tasks[task]
                self.failed_modules.add(
                    FailedModuleFetch(
                        course_id=course_id,
                        module_id=module_id,
                        mod_name=self.MOD_NAME,
                        module_name=entry.get('name', ''),
                        reason=short_error_text(error),
                        failure_kind=failure_kind,
                        retry_exhausted=retry_exhausted,
                        instance_id=entry.get('id', 0) or 0,
                    )
                )
                failed_module_ids.append((course_id, module_id))
                logging.warning(
                    'Failed to load %s module %d in course %d: %s',
                    self.MOD_NAME,
                    module_id,
                    course_id,
                    short_error_text(error),
                )

            if global_error is not None:
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                raise global_error

        return failed_module_ids

    @classmethod
    async def run_async_collect_function_on_list(
        cls,
        entries: List[Dict],
        collect_function,
        collect_kind: str,
        format_mapping: Dict,
    ) -> List[Dict]:
        "Runs a collect function on every entry in a given entries list"
        total_entries = len(entries)
        if total_entries == 0:
            return []
        ctr_digits = int(math.log10(total_entries)) + 1

        async_features = []
        for ctr, entry in enumerate(entries):
            # Example: [ 5/16] Loaded forum discussion 123 "Good discussion"
            loaded_message = (
                f'[%(ctr){ctr_digits}d/%(total){ctr_digits}d] Loaded %(mod_name)s %(collect_kind)s'
                + ' %(collect_id)d "%(collect_name)s"'
            )

            async_features.append(
                run_with_final_message(
                    collect_function,
                    entry,
                    loaded_message,
                    {
                        'ctr': ctr + 1,
                        'total': total_entries,
                        'mod_name': cls.MOD_NAME,
                        'collect_kind': collect_kind,
                        'collect_id': get_nested(entry, format_mapping['collect_id'], 0),
                        'collect_name': get_nested(entry, format_mapping['collect_name'], ''),
                    },
                )
            )

        result = []
        for feature_result in await asyncio.gather(*async_features):
            if isinstance(feature_result, list):
                result.extend(feature_result)
            elif feature_result is not None:
                result.append(feature_result)
        return result

    @staticmethod
    def get_module_in_core_contents(course_id: int, module_id: int, core_contents: Dict[int, List[Dict]]) -> Dict:
        course_section = core_contents.get(course_id, [])
        for section in course_section:
            for module in section.get('modules', []):
                if module.get('id', 0) == module_id:
                    return module
        return {}

    @staticmethod
    def add_module(result: Dict, course_id: int, module_id: int, module: Dict):
        if course_id not in result:
            result[course_id] = {}
        if module_id in result[course_id]:
            logging.warning('Got duplicated module %s in course %s', module_id, course_id)
        result[course_id][module_id] = module
