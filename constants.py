import json
import os
import re
import secrets

from hashlib import blake2b

# API access
if not os.environ.get('CLOUDSCALE_API_TOKEN'):
    raise RuntimeError(
        "No valid API token found in the CLOUDSCALE_API_TOKEN "
        "environment variable"
    )

# The API token is used to distinguish tests from various runners. If you have
# runners that run on a single account at the same time, you should use
# different tokens for each runner, otherwise the first runner may clean up
# the resources of the second runner.
#
# See RUNNER_ID below.
API_TOKEN = os.environ['CLOUDSCALE_API_TOKEN']
API_URL = os.environ.get('CLOUDSCALE_API_URL', 'https://api.cloudscale.ch/v1/')

# One external ping target per IP version, that is assumed to be online
PUBLIC_PING_TARGETS = {
    4: '8.8.8.8',
    6: '2001:4860:4860::8888'
}

# Unique id that distinguishes acceptance tests generated resources.
RUNNER_ID_HASH = blake2b(API_TOKEN.encode("utf-8"), digest_size=8).hexdigest()
RUNNER_ID = f'at-{RUNNER_ID_HASH}'

# Prefix for resources created by this process.
RESOURCE_NAME_PREFIX = PROCESS_ID = f'at-{secrets.token_hex(4)}'

# The worker ID in pytest-xdist, or master in any other case.
WORKER_ID = os.environ.get('PYTEST_XDIST_WORKER', 'master')

# Space that is considered repeated white-space in one-liners
REPEATED_WHITE_SPACE = re.compile(r'\s{2,}')

# Matches an integer or floating point number
NUMBERS = re.compile(r'[0-9]*\.?[0-9]+')

# How many seconds a server may feasibly take to start up
SERVER_START_TIMEOUT = 240

# How many resources may be spawned in parallel in a single call
RESOURCE_CREATION_CONCURRENCY_LIMIT = 4

# Where events are logged
EVENTS_PATH = 'events'

# Where runtime information is stored
RUNTIME_PATH = '.runtime'

# Where locks are stored
LOCKS_PATH = f'{RUNTIME_PATH}/locks'

# Image specific user data overrides (reserved to handle special cases)
IMAGE_SPECIFIC_USER_DATA = {

    # Disable auto-updates in Flatcar (they can cause unexpected reboots)
    re.compile(r'flatcar-[0-9.]+'): json.dumps({
        'ignition': {'version': '2.3.0'},
        'systemd': {
            'units': [
                {'name': 'update-engine.service', 'mask': True},
                {'name': 'locksmithd.service', 'mask': True},
            ]
        }
    }),

    # Disable auto-updates in FCOS (they can cause unexpected reboots)
    re.compile(r'fcos-[0-9]+'): json.dumps({
        'ignition': {'version': '3.0.0'},
        'systemd': {
            'units': [
                {'name': 'zincati.service', 'mask': True},
            ]
        }
    }),
}
