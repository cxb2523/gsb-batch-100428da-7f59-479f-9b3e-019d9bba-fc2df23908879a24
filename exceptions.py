class EndPageException(Exception):
    def __init__(self, message="End of page"):
        self.message = message

    def __str__(self):
        return self.message
