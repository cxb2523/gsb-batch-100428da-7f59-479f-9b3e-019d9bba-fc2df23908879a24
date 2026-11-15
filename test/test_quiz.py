import os
import pickle
import tempfile
import unittest

from dl_coursera.Crawler import Crawler
from dl_coursera.DLTaskGatherer import DLTaskGatherer
from dl_coursera.lib.TaskScheduler import TaskScheduler
from dl_coursera.define import *
from dl_coursera.markup import render_quiz_unavailable

# ---------------------------------------------------------------------------
# Fake Coursera session / GraphQL fixtures
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data

    def raise_for_status(self):
        return None


class _FakeSession:
    """Stand-in for requests.Session; only the endpoints used by crawl_quiz
    and crawl_course are implemented, so no network access is needed."""

    def __init__(self, course2_payload, graphql_handler):
        self._course2_payload = course2_payload
        self._graphql_handler = graphql_handler
        self.graphql_calls = []
        self.start_calls = []

    def get(self, url):
        if 'onDemandCourseMaterials.v2' in url:
            return _FakeResponse(self._course2_payload)
        if 'onDemandCourses.v1' in url:
            return _FakeResponse(
                {
                    'elements': [
                        {'id': 'course-1', 'name': 'Course One', 'slug': 'course-1'}
                    ]
                }
            )
        # course references and everything else: an empty but well-formed page
        return _FakeResponse({'elements': [], 'linked': {'openCourseAssets.v1': []}})

    def post(self, url, json=None):
        self.graphql_calls.append(json)

        operation = json[0]['operationName']
        if operation == 'Submission_StartAttempt':
            self.start_calls.append(json)

        return _FakeResponse(self._graphql_handler(json))


def _course2_payload():
    return {
        'linked': {
            'onDemandCourseMaterialItems.v2': [
                {
                    'id': 'quiz-1',
                    'name': 'Quiz One',
                    'slug': 'quiz-one',
                    'contentSummary': {'typeName': 'quiz'},
                }
            ],
            'onDemandCourseMaterialLessons.v1': [
                {
                    'id': 'lesson-1',
                    'name': 'Lesson One',
                    'slug': 'lesson-one',
                    'itemIds': ['quiz-1'],
                }
            ],
            'onDemandCourseMaterialModules.v1': [
                {
                    'id': 'module-1',
                    'name': 'Module One',
                    'slug': 'module-one',
                    'lessonIds': ['lesson-1'],
                }
            ],
        }
    }


def _draft_fixture():
    return {
        'id': 'draft-1',
        'instructions': {
            'overview': {
                '__typename': 'Submission_HtmlContent',
                'value': '<p>Quiz overview</p>',
            }
        },
        'parts': [
            {
                '__typename': 'Submission_MultipleChoiceQuestion',
                'partId': 'part-1',
                'questionSchema': {
                    'prompt': {
                        '__typename': 'Submission_HtmlContent',
                        'value': '<p>What is 2 + 2?</p>',
                    },
                    'options': [
                        {
                            'optionId': 'o1',
                            'display': {
                                '__typename': 'Submission_HtmlContent',
                                'value': '3',
                            },
                        },
                        {
                            'optionId': 'o2',
                            'display': {
                                '__typename': 'Submission_HtmlContent',
                                'value': '4',
                            },
                        },
                    ],
                },
            }
        ],
    }


def _state_with_draft(draft=None):
    return {
        '__typename': 'Submission_SubmissionState',
        'allowedAction': None,
        'attempts': {
            'inProgressAttempt': {'draft': draft or _draft_fixture()},
            'lastSubmission': None,
        },
    }


def _state_without_attempts():
    return {
        '__typename': 'Submission_SubmissionState',
        'allowedAction': None,
        'attempts': {},
    }


def _state_failure():
    return {
        '__typename': 'Submission_QueryStateFailure',
        'errors': [{'errorCode': 'Not Authorized', '__typename': 'Submission_Error'}],
    }


_START_SUCCESS = {
    '__typename': 'Submission_StartAttemptSuccess',
    'submissionState': {
        'assignment': {'id': 'assignment-1', '__typename': 'Assignment'},
        '__typename': 'SubmissionState',
    },
}

_START_FAILURE = {
    '__typename': 'Submission_StartAttemptFailure',
    'errors': [{'errorCode': 'RATE_LIMITED', '__typename': 'Submission_Error'}],
}


class _GraphQLScript:
    """Answers QueryState / Submission_StartAttempt calls from a fixed script."""

    def __init__(self, *, states, start_result=None):
        # `states` is a list popped in order each time QueryState is called.
        self._states = list(states)
        self._start_result = start_result

    def __call__(self, payload):
        operation = payload[0]['operationName']

        if operation == 'QueryState':
            state = self._states.pop(0)
            return [{'data': {'SubmissionState': {'queryState': state}}}]

        if operation == 'Submission_StartAttempt':
            return [{'data': {'Submission_StartAttempt': self._start_result}}]

        raise AssertionError('unexpected GraphQL operation: %s' % operation)


def _run_quiz_crawl(script):
    sess = _FakeSession(_course2_payload(), script)

    with TaskScheduler() as ts:
        ts.start(n_worker=1)
        crawler = Crawler(ts=ts, sess=sess, include_quiz=True)

        course = Course(id_='course-1', name='Course One', slug='course-1')
        quiz = CourseMaterialQuiz(id_='quiz-1', name='Quiz One', slug='quiz-one')
        crawler.crawl_quiz(course=course, quiz=quiz)
        failures = ts.wait()

    return quiz, sess, failures


def _make_course_with_quiz(quiz):
    lesson = CourseMaterialLesson(id_='lesson-1', name='Lesson One', slug='lesson-one')
    lesson['items'].append(quiz)

    module = CourseMaterialModule(id_='module-1', name='Module One', slug='module-one')
    module['lessons'].append(lesson)

    course = Course(id_='course-1', name='Course One', slug='course-1')
    course['modules'].append(module)
    return course


# ---------------------------------------------------------------------------
# Crawler-level tests
# ---------------------------------------------------------------------------


class TestCrawlQuiz(unittest.TestCase):
    def test_success_with_existing_draft(self):
        script = _GraphQLScript(states=[_state_with_draft()])

        quiz, sess, failures = _run_quiz_crawl(script)

        self.assertEqual(failures, [])
        self.assertEqual(len(quiz['items']), 1)

        item = quiz['items'][0]
        self.assertEqual(item['type'], 'CML')
        # existing rendering must be preserved
        self.assertIn('What is 2 + 2?', item['html'])
        self.assertIn('type="radio"', item['html'])
        self.assertEqual(len(item['assets']), 0)

        # a draft already exists, so no attempt is started
        self.assertEqual(sess.start_calls, [])
        self.assertEqual(len(sess.graphql_calls), 1)

    def test_start_attempt_failure_creates_placeholder(self):
        script = _GraphQLScript(
            states=[_state_without_attempts()],
            start_result=_START_FAILURE,
        )

        quiz, sess, failures = _run_quiz_crawl(script)

        self.assertEqual(failures, [])
        self.assertEqual(len(quiz['items']), 1)

        item = quiz['items'][0]
        self.assertEqual(item['type'], 'QuizUnavailable')
        self.assertEqual(item['reason'], QUIZ_UNAVAILABLE_START_FAILED)
        self.assertEqual(item['detail'], 'RATE_LIMITED')

        self.assertEqual(len(sess.start_calls), 1)
        self.assertEqual(len(sess.graphql_calls), 2)

    def test_no_draft_after_successful_start_creates_placeholder(self):
        script = _GraphQLScript(
            states=[_state_without_attempts(), _state_without_attempts()],
            start_result=_START_SUCCESS,
        )

        quiz, sess, failures = _run_quiz_crawl(script)

        self.assertEqual(failures, [])
        self.assertEqual(len(quiz['items']), 1)

        item = quiz['items'][0]
        self.assertEqual(item['type'], 'QuizUnavailable')
        self.assertEqual(item['reason'], QUIZ_UNAVAILABLE_NO_DRAFT)
        self.assertIsNone(item['detail'])

        self.assertEqual(len(sess.start_calls), 1)
        self.assertEqual(len(sess.graphql_calls), 3)

    def test_bad_state_creates_placeholder_without_starting_attempt(self):
        script = _GraphQLScript(states=[_state_failure()])

        quiz, sess, failures = _run_quiz_crawl(script)

        self.assertEqual(failures, [])
        self.assertEqual(len(quiz['items']), 1)

        item = quiz['items'][0]
        self.assertEqual(item['type'], 'QuizUnavailable')
        self.assertEqual(item['reason'], QUIZ_UNAVAILABLE_STATE)
        self.assertEqual(item['detail'], 'Not Authorized')

        self.assertEqual(sess.start_calls, [])
        self.assertEqual(len(sess.graphql_calls), 1)

    def test_placeholder_and_real_content_are_mutually_exclusive(self):
        scenarios = [
            # success -> exactly one CML item, no placeholder
            (_GraphQLScript(states=[_state_with_draft()]), 'CML'),
            # start failure -> exactly one placeholder, no CML
            (
                _GraphQLScript(
                    states=[_state_without_attempts()],
                    start_result=_START_FAILURE,
                ),
                'QuizUnavailable',
            ),
            # no draft -> exactly one placeholder, no CML
            (
                _GraphQLScript(
                    states=[_state_without_attempts(), _state_without_attempts()],
                    start_result=_START_SUCCESS,
                ),
                'QuizUnavailable',
            ),
            # bad state -> exactly one placeholder, no CML
            (_GraphQLScript(states=[_state_failure()]), 'QuizUnavailable'),
        ]

        for script, expected_type in scenarios:
            with self.subTest(expected_type=expected_type):
                quiz, _sess, failures = _run_quiz_crawl(script)

                self.assertEqual(failures, [])
                self.assertEqual(len(quiz['items']), 1)
                self.assertEqual(quiz['items'][0]['type'], expected_type)

                types = {_['type'] for _ in quiz['items']}
                self.assertEqual(types, {expected_type})
                self.assertNotIn(
                    'CML' if expected_type == 'QuizUnavailable' else 'QuizUnavailable',
                    types,
                )

    def test_rerun_replaces_placeholder_with_real_content(self):
        # first crawl: the attempt cannot be started -> placeholder
        script = _GraphQLScript(
            states=[_state_without_attempts()],
            start_result=_START_FAILURE,
        )
        quiz, _sess, failures = _run_quiz_crawl(script)
        self.assertEqual(failures, [])
        self.assertEqual(quiz['items'][0]['type'], 'QuizUnavailable')

        # the placeholder survives the crawl.pkl cache round-trip, so a later
        # run still recognizes the quiz as previously unavailable
        restored = pickle.loads(pickle.dumps(quiz))
        self.assertEqual(restored['name'], 'Quiz One')
        self.assertEqual(restored['slug'], 'quiz-one')
        self.assertEqual(restored['items'][0]['type'], 'QuizUnavailable')

        # re-run once the quiz is reachable: the placeholder is replaced in
        # place on the same (cached) quiz object, not appended to
        script2 = _GraphQLScript(states=[_state_with_draft()])
        course2 = Course(id_='course-1', name='Course One', slug='course-1')
        sess2 = _FakeSession(_course2_payload(), script2)

        with TaskScheduler() as ts:
            ts.start(n_worker=1)
            crawler2 = Crawler(ts=ts, sess=sess2, include_quiz=True)
            crawler2.crawl_quiz(course=course2, quiz=restored)
            failures2 = ts.wait()

        self.assertEqual(failures2, [])
        self.assertEqual(len(restored['items']), 1)
        self.assertEqual(restored['items'][0]['type'], 'CML')
        self.assertIn('What is 2 + 2?', restored['items'][0]['html'])
        self.assertEqual(sess2.start_calls, [])

    def test_repeated_failure_keeps_a_single_placeholder(self):
        # re-crawling an already-placeholder quiz that still fails must not
        # stack a second placeholder entry
        quiz = CourseMaterialQuiz(id_='quiz-1', name='Quiz One', slug='quiz-one')
        course = Course(id_='course-1', name='Course One', slug='course-1')

        for _ in range(3):
            script = _GraphQLScript(
                states=[_state_without_attempts()],
                start_result=_START_FAILURE,
            )
            sess = _FakeSession(_course2_payload(), script)
            with TaskScheduler() as ts:
                ts.start(n_worker=1)
                crawler = Crawler(ts=ts, sess=sess, include_quiz=True)
                crawler.crawl_quiz(course=course, quiz=quiz)
                failures = ts.wait()
            self.assertEqual(failures, [])

        self.assertEqual(len(quiz['items']), 1)
        self.assertEqual(quiz['items'][0]['type'], 'QuizUnavailable')
        self.assertEqual(quiz['items'][0]['reason'], QUIZ_UNAVAILABLE_START_FAILED)

    def test_failed_quiz_stays_in_course_catalog(self):
        script = _GraphQLScript(
            states=[_state_without_attempts()],
            start_result=_START_FAILURE,
        )
        sess = _FakeSession(_course2_payload(), script)

        with TaskScheduler() as ts:
            ts.start(n_worker=1)
            crawler = Crawler(ts=ts, sess=sess, include_quiz=True)
            course = Course(slug='course-1')
            crawler.crawl_course(course=course)
            failures = ts.wait()

        self.assertEqual(failures, [])

        quizzes = [
            item
            for module in course['modules']
            for lesson in module['lessons']
            for item in lesson['items']
            if item['type'] == 'Quiz'
        ]
        self.assertEqual(len(quizzes), 1)

        quiz = quizzes[0]
        self.assertEqual(quiz['slug'], 'quiz-one')
        self.assertEqual(quiz['name'], 'Quiz One')
        self.assertEqual(len(quiz['items']), 1)
        self.assertEqual(quiz['items'][0]['type'], 'QuizUnavailable')


# ---------------------------------------------------------------------------
# DLTaskGatherer-level tests
# ---------------------------------------------------------------------------


def _walk_files(root):
    result = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for filename in filenames:
            result.append(os.path.join(dirpath, filename))
    return result


class TestGatherQuiz(unittest.TestCase):
    def test_placeholder_produces_html_but_no_download_tasks(self):
        quiz = CourseMaterialQuiz(id_='quiz-1', name='Quiz One', slug='quiz-one')
        quiz['items'].append(
            CourseMaterialQuizUnavailableItem(
                reason=QUIZ_UNAVAILABLE_START_FAILED, detail='RATE_LIMITED'
            )
        )
        course = _make_course_with_quiz(quiz)

        with tempfile.TemporaryDirectory() as outdir:
            dl_tasks = DLTaskGatherer(soc=course, outdir=outdir).gather()

            # no resources to download: in particular no task with an empty URL
            self.assertEqual(dl_tasks, [])

            files = _walk_files(outdir)
            html_files = [
                f
                for f in files
                if f.endswith('.html')
                and os.path.join('resource', '') not in f + os.sep
            ]
            self.assertEqual(len(html_files), 1)

            with open(html_files[0], encoding='UTF-8') as ifs:
                page = ifs.read()

            self.assertIn('Quiz One', page)
            self.assertIn('temporarily unavailable', page)
            self.assertIn('Starting a quiz attempt failed.', page)
            self.assertIn('RATE_LIMITED', page)

            # placeholders must not create a playlist either
            self.assertFalse(any(os.path.basename(f) == 'playlist.m3u' for f in files))

    def test_placeholder_does_not_pollute_mixed_download_list(self):
        # one unavailable quiz alongside a real lecture video
        quiz = CourseMaterialQuiz(id_='quiz-1', name='Quiz One', slug='quiz-one')
        quiz['items'].append(
            CourseMaterialQuizUnavailableItem(reason=QUIZ_UNAVAILABLE_NO_DRAFT)
        )

        lecture = CourseMaterialLecture(
            id_='lecture-1', name='Lecture One', slug='lecture-one'
        )
        lecture['videos'].append(Video('https://example.org/video.mp4', subtitles={}))

        lesson = CourseMaterialLesson(
            id_='lesson-1', name='Lesson One', slug='lesson-one'
        )
        lesson['items'] = [lecture, quiz]

        module = CourseMaterialModule(
            id_='module-1', name='Module One', slug='module-one'
        )
        module['lessons'].append(lesson)

        course = Course(id_='course-1', name='Course One', slug='course-1')
        course['modules'].append(module)

        with tempfile.TemporaryDirectory() as outdir:
            dl_tasks = DLTaskGatherer(soc=course, outdir=outdir).gather()

            self.assertEqual(len(dl_tasks), 1)
            self.assertTrue(dl_tasks[0]['url'])
            self.assertEqual(dl_tasks[0]['url'], 'https://example.org/video.mp4')
            self.assertTrue(dl_tasks[0]['filename'])

            files = _walk_files(outdir)
            self.assertTrue(any(os.path.basename(f) == 'playlist.m3u' for f in files))
            self.assertTrue(any(f.endswith('quiz-one.html') for f in files))

    def test_successful_quiz_gathers_assets_and_html_only(self):
        quiz = CourseMaterialQuiz(id_='quiz-1', name='Quiz One', slug='quiz-one')
        quiz['items'].append(
            CourseMaterialSupplementItemCML(
                html='<p>What is 2 + 2?</p>',
                assets=[
                    Asset(
                        id_='asset-1',
                        url='https://example.org/diagram.png',
                        name='diagram.png',
                    )
                ],
            )
        )
        course = _make_course_with_quiz(quiz)

        with tempfile.TemporaryDirectory() as outdir:
            dl_tasks = DLTaskGatherer(soc=course, outdir=outdir).gather()

            self.assertEqual(len(dl_tasks), 1)
            self.assertEqual(dl_tasks[0]['url'], 'https://example.org/diagram.png')
            self.assertTrue(dl_tasks[0]['filename'].endswith('diagram.png'))

            files = _walk_files(outdir)
            html_files = [
                f
                for f in files
                if f.endswith('.html')
                and os.path.join('resource', '') not in f + os.sep
            ]
            self.assertEqual(len(html_files), 1)
            with open(html_files[0], encoding='UTF-8') as ifs:
                page = ifs.read()
            self.assertIn('What is 2 + 2?', page)
            self.assertNotIn('temporarily unavailable', page)


class TestRenderQuizUnavailable(unittest.TestCase):
    def test_detail_is_html_escaped(self):
        html = render_quiz_unavailable(
            reason=QUIZ_UNAVAILABLE_STATE, detail='<script>alert(1)</script>'
        )
        self.assertNotIn('<script>alert(1)</script>', html)
        self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt;', html)
        self.assertIn('quiz-unavailable', html)

    def test_all_reasons_have_messages(self):
        for reason in QUIZ_UNAVAILABLE_REASONS:
            with self.subTest(reason=reason):
                html = render_quiz_unavailable(reason=reason)
                self.assertIn('temporarily unavailable', html)


if __name__ == '__main__':
    unittest.main()
