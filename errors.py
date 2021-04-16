class AcceptanceTestException(Exception):
    """ Generic exception base class for error conditons in the acceptance test
    framework.

    """


class ServerError(AcceptanceTestException):
    """ Error during server operations

    """


class Timeout(ServerError):
    """ Timeout during server creation / connection to the host after reboot.

    """
