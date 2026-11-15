class Episode:
	"""Create Episode object. Contains information of an episode."""
	def __init__(self, title=None, url=None, current_url=None, current_page=0,
			skip=False, complete=False, total=0, image=None, pause=False):
		"""Construct."""
		self.title = title
		self.url = url
		self.current_url = current_url
		# position of images on current page, starting from 1.
		# FIXME: should we rename this to current_image_index?
		self.current_page = current_page
		self.skip = skip
		self.complete = complete
		# paused by a recoverable error (e.g. 429, IncompleteRead); the
		# episode keeps its .part fragments and can be resumed.
		self.pause = pause
		# total number of images in this episode
		self.total = total
		self.image = image
