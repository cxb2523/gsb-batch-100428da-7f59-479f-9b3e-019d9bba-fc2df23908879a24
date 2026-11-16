"""All exceptions for RenfeBot"""

class RenfeBotException(Exception):
    """Base exception for the rest of the program"""
    pass


class StationNotFound(RenfeBotException):
    """Raise when the station provided is not found at the Stations file"""
    pass

class InvalidDWRToken(RenfeBotException):
    """Raise when the DWR token provided is invalid"""
    pass

class InvalidTrainRideFilter(RenfeBotException):
    """Raise when the filter input by the user didn't return any result, available or not"""
    pass


class InvalidTrainListResponse(RenfeBotException):
    """Raise when the getTrainsList DWR response doesn't contain a parseable train list.

    This covers both a body without the expected r.handleCallback fragment and a payload
    whose JSON cannot be decoded.
    """
    pass


class TrainRideParsingError(RenfeBotException):
    """Raise when a train entry from Renfe cannot be turned into a TrainRideRecord.

    The rides parsed before the failing entry are preserved so callers can still use the
    partial result.
    """

    def __init__(self, message: str, partial_rides=None):
        super().__init__(message)
        self.partial_rides = partial_rides or []
