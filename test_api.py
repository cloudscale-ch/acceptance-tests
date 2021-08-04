"""

API Functionality
=================

Test the basic API functionality independent of any specific API actions.

"""
import requests

from constants import API_URL
from util import raw_headers


def test_duplicate_headers():
    """ Check for duplicate headers (same key and value).

    In general the same header field name may appear multiple times in a HTTP
    response according to RFC 2616 section 4.2. But sending the exact same
    header multiple times is always a misconfiguration. There is no need to
    send the same value twice.

    """

    # Look for duplicate field values
    for field_name, field_values in raw_headers(API_URL).items():
        for value in field_values:
            assert field_values.count(value) == 1


def test_invalid_duplicate_headers():
    """ Check there are no conflicting headers.

    Only whitelisted header field names are allowed to appear multiple times
    (with different values) in the response.

    """

    # List of header field names that are allowed multiple times
    allowed_duplicate_field_names = (

        # The "Vary" conditions may be influenced by different (reverse)
        # proxies in the delivery chain.
        'Vary',
    )

    # Each field name that has multiple values must be whitelisted
    for field_name, field_values in raw_headers(API_URL).items():
        if len(field_values) > 1:
            assert field_name in allowed_duplicate_field_names


def test_cors_headers():
    """ Check for the correct list of CORS headers.

    The API should allow CORS (Cross Origin Resource Sharing) to allow other
    sites to embed the API. This is required for tools like Rancher to work
    with our API. This must also work for non 20X status codes.

    """

    # Check CORS headers for a valid and invalid (HTTP 404) URL
    for url in (API_URL, f'{API_URL}/invalid'):
        headers = requests.get(url).headers

        # Allow browsers to query the API from any origin
        assert headers['Access-Control-Allow-Origin'] == '*'

        # Allow Content-Type and Authorization headers
        assert headers['Access-Control-Allow-Headers'] \
            == 'Content-Type, Authorization'

        # Allow all methods supported by the API
        assert headers['Access-Control-Allow-Methods'] \
            == 'GET, POST, OPTIONS, PUT, PATCH, DELETE'
