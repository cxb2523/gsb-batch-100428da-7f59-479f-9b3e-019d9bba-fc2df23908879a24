# from requests import HTTPError
from curl_cffi.requests.exceptions import HTTPError
from urllib3.exceptions import IncompleteRead

class ComicCrawlerSignal(BaseException):
	"""Extend BaseException."""
	pass

class LastPageError(ComicCrawlerSignal):
	"""Raise LastPageError to exit crawl pages loop."""
	pass

class SkipEpisodeError(ComicCrawlerSignal):
	"""Raise SkipEpisodeError to exit crawl episodes loop."""
	def __init__(self, always=True):
		super().__init__()
		self.always = always

class PauseEpisodeError(ComicCrawlerSignal):
	"""Raise PauseEpisodeError to pause current episode while keeping the
	download progress, so the mission can be resumed later."""
	def __init__(self, err):
		super().__init__()
		self.err = err

class PauseDownloadError(ComicCrawlerSignal):
	"""Raise PauseDownloadError to exit crawl mission loop."""
	pass
	
class SkipPageError(ComicCrawlerSignal):
	"""Raise this error to skip a page in the analyzer."""
	pass

# class ExitErrorLoop(ComicCrawlerSignal):
	# """Raise ExitErrorLoop to exit error loop."""
	# pass

class ComicCrawlerError(Exception):
	"""Extend Exception."""
	pass

class ModuleError(ComicCrawlerError):
	"""Can't find module."""
	pass

def is_403(err):
	return is_http(err, code=403)

def is_recoverable(err):
	"""Check if the error is temporary and the download can be resumed later.

	Rate limiting / service-unavailable responses and interrupted transfers
	(IncompleteRead) leave a valid .part fragment behind, so the page can be
	continue-downloaded through a Range request.
	"""
	if isinstance(err, IncompleteRead):
		return True
	if not is_http(err):
		return False
	status = getattr(getattr(err, "response", None), "status_code", None)
	return status in (423, 429, 503)

def is_http(err, code=None):
	if not isinstance(err, HTTPError):
		return False
		
	if code is None:
		return True
		
	try:
		if err.response.status_code == code:
			return True
	except AttributeError:
		pass
		
	return False
