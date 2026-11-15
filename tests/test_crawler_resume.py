"""Resume / per-page outcome tests for comiccrawler.crawler.

All downloads are served from in-memory fakes (see conftest.FakeGrabber);
no real network or site is involved.
"""

from pathlib import Path

import pytest
from curl_cffi.requests.exceptions import HTTPError
from urllib3.exceptions import IncompleteRead

from conftest import (
	FakeGrabber,
	event_data,
	make_mission,
	payload_for,
)
from comiccrawler import crawler as crawler_mod
from comiccrawler.crawler import (
	download,
	EP_PAUSED,
	EP_COMPLETE,
	EP_SKIPPED,
)
from comiccrawler.crawler import Crawler
from comiccrawler.error import PauseEpisodeError, is_recoverable


def install_grabber(mission, grabber, monkeypatch):
	"""Make every Crawler created for the mission use the fake grabber."""
	original_init = Crawler.__init__

	def patched_init(self, m, ep, savepath):
		original_init(self, m, ep, savepath)
		if m is mission:
			self.downloader = grabber

	monkeypatch.setattr(Crawler, "__init__", patched_init)


def http_429():
	response = type(
		"Response",
		(),
		{"status_code": 429, "headers": {}, "history": []},
	)()
	return HTTPError("rate limited", response=response)


def output_files(savepath, mission):
	folder = Path(savepath) / mission.title / "ep-1"
	return sorted(p.name for p in folder.iterdir())


def part_file(savepath, mission, page):
	return (
		Path(savepath)
		/ mission.title
		/ "ep-1"
		/ "{:03d}.part".format(page)
	)


def final_file(savepath, mission, page):
	directory = Path(savepath) / mission.title / "ep-1"
	matches = list(directory.glob("{:03d}.*".format(page)))
	return matches[0] if matches else None


def test_429_keeps_part_and_pauses(tmp_path, monkeypatch, event_channel):
	"""A mid-download 429 retains the .part fragment and the episode ends
	paused instead of failing the whole mission."""
	_channel, recorder = event_channel
	savepath = tmp_path / "download"
	mission, images = make_mission(tmp_path, image_count=3)

	page1_url, page1 = payload_for(images, 1)
	page2_url, page2 = payload_for(images, 2)
	page3_url, page3 = payload_for(images, 3)
	payloads = {
		page1_url: page1,
		page2_url: page2,
		page3_url: page3,
	}

	def fail_page2(attempt, headers, tempfile):
		# emulate grabimg: some bytes land in the .part file first, then
		# the server responds 429.
		Path(tempfile).parent.mkdir(parents=True, exist_ok=True)
		with open(tempfile, "wb") as f:
			f.write(page2[:3])
		return http_429()

	grabber = FakeGrabber(payloads, fail={page2_url: fail_page2})
	install_grabber(mission, grabber, monkeypatch)

	download(mission, str(savepath))

	# mission is resumable, not failed
	assert mission.state == "PAUSE"
	ep = mission.episodes[0]
	assert ep.pause is True
	assert ep.complete is False

	# page 1 finished, page 2 keeps its fragment, page 3 never started
	files = output_files(savepath, mission)
	assert "001.jpg" in files
	assert "002.part" in files
	assert "003.part" not in files
	assert part_file(savepath, mission, 2).read_bytes() == page2[:3]

	# the page was retried up to the error_loop limit, each attempt saw 429
	assert grabber.attempts[page2_url] == 5
	assert grabber.calls.count(page2_url) == 5

	# checkable episode event with page-level outcome
	results = event_data(recorder, "DOWNLOAD_EP_RESULT")
	assert len(results) == 1
	result = results[0]
	assert result.outcome == EP_PAUSED
	assert result.title == "ep-1"
	assert result.saved_count == 1
	assert result.existed_count == 0
	assert result.success_count == 1
	assert result.skipped_count == 0
	assert result.paused_pages == [2]
	assert result.paused_reason == "HTTP 429"

	pause_events = event_data(recorder, "DOWNLOAD_PAUSE")
	assert pause_events == [mission]
	# no failure event is emitted for a recoverable pause
	assert not event_data(recorder, "DOWNLOAD_ERROR")


def test_resume_from_part_completes(tmp_path, monkeypatch, event_channel):
	"""On resume, a .part fragment is continued through a Range request and
	lands as a complete file; a fragment alone is never treated as done."""
	_channel, recorder = event_channel
	savepath = tmp_path / "download"
	mission, images = make_mission(tmp_path, image_count=2)

	page1_url, page1 = payload_for(images, 1)
	page2_url, page2 = payload_for(images, 2)
	payloads = {page1_url: page1, page2_url: page2}

	# simulate the state left by the previous paused run:
	# page 1 complete, page 2 only a fragment
	folder = Path(savepath) / mission.title / "ep-1"
	folder.mkdir(parents=True)
	(folder / "001.jpg").write_bytes(page1)
	(folder / "002.part").write_bytes(page2[:4])

	grabber = FakeGrabber(payloads)
	install_grabber(mission, grabber, monkeypatch)

	download(mission, str(savepath))

	assert mission.state == "FINISHED"
	ep = mission.episodes[0]
	assert ep.complete is True
	assert ep.pause is False

	# the fragment was continued, not restarted
	assert grabber.range_requests == [
		(page2_url, "bytes=4-")
	]
	assert not (folder / "002.part").exists()
	page2_output = final_file(savepath, mission, 2)
	assert page2_output is not None
	assert page2_output.read_bytes() == page2

	results = event_data(recorder, "DOWNLOAD_EP_RESULT")
	result = results[0]
	assert result.outcome == EP_COMPLETE
	# page 1 already existed -> counted once, page 2 resumed -> saved once
	assert result.existed_count == 1
	assert result.saved_count == 1
	assert result.success_count == 2
	assert result.paused_pages == []
	assert result.skipped_count == 0


def test_existing_page_skipped_without_redownload(
	tmp_path, monkeypatch, event_channel
):
	"""A fully downloaded page is skipped on resume and never re-requested."""
	_channel, recorder = event_channel
	savepath = tmp_path / "download"
	mission, images = make_mission(tmp_path, image_count=2)

	page1_url, page1 = payload_for(images, 1)
	page2_url, page2 = payload_for(images, 2)
	payloads = {page1_url: page1, page2_url: page2}

	folder = Path(savepath) / mission.title / "ep-1"
	folder.mkdir(parents=True)
	(folder / "001.jpg").write_bytes(page1)

	grabber = FakeGrabber(payloads)
	install_grabber(mission, grabber, monkeypatch)

	download(mission, str(savepath))

	assert mission.state == "FINISHED"
	# page 1 was never requested again
	assert page1_url not in grabber.calls
	assert grabber.calls == [page2_url]
	assert (folder / "001.jpg").read_bytes() == page1
	assert final_file(savepath, mission, 2).read_bytes() == page2

	results = event_data(recorder, "DOWNLOAD_EP_RESULT")
	result = results[0]
	assert result.outcome == EP_COMPLETE
	assert result.existed_count == 1
	assert result.saved_count == 1
	assert result.success_count == 2
	assert result.skipped_count == 0
	assert result.paused_pages == []


def test_incomplete_read_is_recoverable():
	"""IncompleteRead must be classified as a resumable error."""
	assert is_recoverable(IncompleteRead(5, 10))
	assert is_recoverable(
		HTTPError("x", response=type(
			"R", (), {"status_code": 429}
		)())
	)
	assert not is_recoverable(ValueError("Bad file type: .exe"))
	assert not is_recoverable(
		HTTPError("x", response=type(
			"R", (), {"status_code": 404}
		)())
	)


def test_bad_file_type_skips_episode(tmp_path, monkeypatch, event_channel):
	"""An illegal file type stays an unrecoverable SkipEpisodeError outcome;
	the exception semantics are not swallowed by the new pause flow."""
	_channel, recorder = event_channel
	savepath = tmp_path / "download"
	mission, images = make_mission(tmp_path, image_count=2)

	page1_url, page1 = payload_for(images, 1)
	page2_url, _page2 = payload_for(images, 2)
	payloads = {page1_url: page1, page2_url: b"dummy"}

	grabber = FakeGrabber(payloads)
	install_grabber(mission, grabber, monkeypatch)

	original_download_image = crawler_mod.Crawler.download_image

	def patched_download_image(self):
		if self.image.url == page2_url:
			# mirror download_image's invalid-extension rejection
			raise ValueError("Bad file type: .exe")
		return original_download_image(self)

	monkeypatch.setattr(
		crawler_mod.Crawler, "download_image", patched_download_image
	)

	# crawl() is where SkipEpisodeError is handled; download() would treat
	# the unfinished episode as a mission failure (unchanged behavior).
	crawler_mod.crawl(mission, str(savepath))

	ep = mission.episodes[0]
	# always=False -> episode is retried later, not permanently skipped
	assert ep.skip is False
	assert ep.complete is False
	assert ep.pause is False
	# nothing resumable was claimed as a finished page for page 2
	assert not final_file(savepath, mission, 2)

	results = event_data(recorder, "DOWNLOAD_EP_RESULT")
	result = results[0]
	assert result.outcome == EP_SKIPPED
	assert result.saved_count == 1
	assert result.existed_count == 0
	assert result.success_count == 1
	assert result.skipped_count == 1
	assert result.paused_pages == []
	# the page was retried up to the error_loop limit before being skipped
	assert grabber.attempts[page1_url] == 1
	assert result.paused_reason is None


def test_incomplete_read_resumes_with_fragment(
	tmp_path, monkeypatch, event_channel
):
	"""An IncompleteRead mid-transfer keeps the fragment and pauses; a later
	run continues it exactly like the 429 case."""
	_channel, recorder = event_channel
	savepath = tmp_path / "download"
	mission, images = make_mission(tmp_path, image_count=1)

	page1_url, page1 = payload_for(images, 1)
	payloads = {page1_url: page1}

	def fail_incomplete(attempt, headers, tempfile):
		Path(tempfile).parent.mkdir(parents=True, exist_ok=True)
		with open(tempfile, "wb") as f:
			f.write(page1[:5])
		return IncompleteRead(5, len(page1) - 5)

	grabber = FakeGrabber(
		payloads, fail={page1_url: fail_incomplete}
	)
	install_grabber(mission, grabber, monkeypatch)

	with pytest.raises(PauseEpisodeError):
		crawler_mod.crawlpage(
			crawler_mod.Crawler(
				mission, mission.episodes[0], str(savepath)
			)
		)

	assert part_file(savepath, mission, 1).read_bytes() == page1[:5]
	assert grabber.attempts[page1_url] == 5

	# second run: fragment continued via Range, episode completes
	grabber2 = FakeGrabber(payloads)
	install_grabber(mission, grabber2, monkeypatch)
	download(mission, str(savepath))

	assert mission.state == "FINISHED"
	assert grabber2.range_requests == [(page1_url, "bytes=5-")]
	assert final_file(savepath, mission, 1).read_bytes() == page1

	results = event_data(recorder, "DOWNLOAD_EP_RESULT")
	assert results[-1].outcome == EP_COMPLETE
	assert results[-1].saved_count == 1
	assert results[-1].existed_count == 0
	assert results[-1].success_count == 1
