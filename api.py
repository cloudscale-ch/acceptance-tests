import boto3
import re
import requests
import time

from constants import API_TOKEN
from constants import API_URL
from constants import LOCKS_PATH
from constants import PROCESS_ID
from constants import RUNNER_ID
from errors import Timeout
from events import trigger
from filelock import FileLock
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry
from resources import ObjectsUser
from urllib.parse import urlparse


# Contains delete handlers, see delete_handler below
DELETE_HANDLERS = {}


class CloudscaleHTTPAdapter(HTTPAdapter):
    """ An HTTP adapter that serialises requests to the cloudscale.ch API,
    across multiple processes.

    """

    def __init__(self, *args, **kwargs):
        self.lock = FileLock(f'{LOCKS_PATH}/{RUNNER_ID}.lock')
        super().__init__(*args, **kwargs)

    def send(self, request, *args, **kwargs):
        with self.lock:
            return super().send(request, *args, **kwargs)


class RetryStrategy(Retry):
    """ We retry certain requests using urllib3's Retry class, but we
    need to be quite explicit about when, in a way that is more complicated
    than Retry's config approach.

    """

    def is_retry(self, method, status_code, *args, **kwargs) -> bool:
        """ This method has to decide if a retry should be attempted, it does
        not need to know how many retries have been done already.

        """

        # DELETE may fail on resources during cleanup, as they may be
        # still be in the process of being created.
        if status_code == 400 and method == 'DELETE':
            return True

        # Maintenances are always retried.
        if status_code == 503:
            return True

        # Fallback to the default retry handling otherwise (which supports
        # the Retry-After header).
        return super().is_retry(method, status_code, *args, **kwargs)


class API(requests.Session):
    """ A primitive API client to the cloudscale.ch REST API.

    * Uses keep-alive to limit repeated handshakes.
    * Removes the need to pass the URL for every request.
    * Responses are always checked for their status.
    * Resources created by an API instance and a given token are tagged.
    * Tagged resources can be cleaned up.

    """

    def __init__(self, scope, zone=None, read_only=False):
        super().__init__()

        if zone:
            agent_suffix = f' ({zone})'
        else:
            agent_suffix = ''

        self.api_url = API_URL
        self.headers['Authorization'] = f'Bearer {API_TOKEN}'
        self.headers['User-Agent'] = f'Acceptance Tests{agent_suffix}'
        self.hooks = {'response': self.on_response}
        self.scope = scope
        self.read_only = read_only
        self.zone = zone

        # 8 Retries @ 2.5 backoff_factor = 10.6 minutes
        retry_strategy = RetryStrategy(
            total=8,
            backoff_factor=2.5
        )

        adapter = CloudscaleHTTPAdapter(
            max_retries=retry_strategy,
        )

        self.mount("https://", adapter)

        # This is None, when running "invoke cleanup"
        if self.zone:
            self.objects_endpoint = self.objects_endpoint_for(self.zone)

    def post(self, url, data=None, json=None, add_tags=True, **kwargs):
        assert not data, "Please only use json, not data"

        if json and add_tags:
            json['tags'] = {
                'runner': RUNNER_ID,
                'process': PROCESS_ID,
                'scope': self.scope,
                'zone': self.zone,
            }

        return super().post(url, data=data, json=json, **kwargs)

    def delete(self, url):
        delete_handler_for_url(url)(api=self, url=url)

    def on_response(self, response, *args, **kwargs):
        trigger('request.after', request=response.request, response=response)

        try:
            response.raise_for_status()
        except requests.exceptions.HTTPError as e:
            ignore = e.request.method == 'DELETE' \
                and e.response.status_code == 404

            if not ignore:
                raise e

    def request(self, method, url, *args, **kwargs):
        if self.read_only and method not in ('HEAD', 'GET'):
            raise RuntimeError(f"Trying to run {method} on read-only API")

        if not url.startswith(self.api_url):
            url = f'{self.api_url}/{url.lstrip("/")}'

        return super().request(method, url, *args, **kwargs)

    def resources(self, path):
        return self.get(f'{path}?tag:runner={RUNNER_ID}').json()

    def runner_resources(self):
        """ Returns all resources created by the current API token as part
        of an acceptance test.

        """

        def resources(path):
            return self.get(f'{path}?tag:runner={RUNNER_ID}').json()

        yield from resources('/volume-snapshots')
        yield from resources('/servers')
        yield from resources('/load-balancers')
        yield from resources('/volumes')
        yield from resources('/floating-ips')
        yield from resources('/subnets')
        yield from resources('/networks')
        yield from resources('/server-groups')
        yield from resources('/custom-images')
        yield from resources('/objects-users')

    def cleanup(self, limit_to_scope=True, limit_to_process=True):
        """ Deletes resources created by this API object. """

        exceptions = []

        for r in self.runner_resources():
            assert r['tags']['runner'] == RUNNER_ID

            if limit_to_scope and r['tags']['scope'] != self.scope:
                continue

            if limit_to_process and r['tags']['process'] != PROCESS_ID:
                continue

            try:
                self.delete(r['href'])
            except Exception as e:
                exceptions.append(e)

        if exceptions:
            raise ExceptionGroup("Failures during cleanup.", exceptions)

    def objects_endpoint_for(self, zone):
        netloc = urlparse(self.api_url).netloc

        if netloc.startswith("api"):
            prefix = ""
            tld = "ch"
        else:
            prefix = f"{netloc.split('-')[0]}-"
            tld = "zone"

        return f"{prefix}objects.{zone.rstrip('012345679')}.cloudscale.{tld}"


def delete_handler(path):
    """ Registers the decorated function as delete handler for the given
    path. The path is treated as regular expression pattern, used to search
    the path (not the whole URL).

    The decorated function is supposed to delete the given URL.

    """

    def delete_handler_decorator(fn):
        DELETE_HANDLERS[path] = fn
        return fn
    return delete_handler_decorator


def delete_handler_for_url(url):
    """ Evaluates the registered delete handlers and picks the first matching
    one, or a default.

    The order of the evaluation is not strictly defined, handlers are
    expected to not overlap.

    """
    path = urlparse(url).path

    for pattern, fn in DELETE_HANDLERS.items():
        if re.fullmatch(pattern, path):
            return fn

    # Use the low-level method, as we are downstream from api.delete and cannot
    # call api.delete, lest we want an infinite loop.
    return lambda api, url: api.request("DELETE", url)


@delete_handler(path='/v1/volume-snapshots/.+')
def delete_volume_snapshots(api, url):
    """ When deleting volume-snapshots, we need to wait for the snapshots to
    be deleted, or we won't be able to delete the servers later.

    """

    # Delete the snapshot first
    api.request("DELETE", url)

    # Wait for snapshots to be deleted
    timeout = time.monotonic() + 60

    while time.monotonic() < timeout:
        time.sleep(1)

        try:
            snapshot = api.get(url)
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                # The snapshot is gone, stop waiting
                break
            else:
                raise e

    else:
        raise Timeout(
            f'Snapshot failed to delete within 60 seconds. Status '
            f'is still "{snapshot.json()["status"]}".'
        )


@delete_handler(path='/v1/objects-users/.+')
def delete_objects_users(api, url):
    """ Before deleting an objects user, we have to delete owned buckets. """

    user = ObjectsUser.from_href(None, api, url, name="")
    user.wait_for_access()

    session = boto3.Session(
        aws_access_key_id=user.keys[0]['access_key'],
        aws_secret_access_key=user.keys[0]['secret_key'],
    )

    objects_endpoint = api.objects_endpoint_for(zone=user.tags['zone'])
    s3 = session.resource('s3', endpoint_url=f"https://{objects_endpoint}")

    for bucket in s3.buckets.all():
        bucket.objects.all().delete()
        bucket.delete()

    sns = boto3.client(
        'sns',
        endpoint_url=f"https://{objects_endpoint}",
        aws_access_key_id=user.keys[0]['access_key'],
        aws_secret_access_key=user.keys[0]['secret_key'],
        region_name='default',
    )

    for topic in sns.list_topics().get('Topics', ()):
        arn = topic["TopicArn"]
        assert re.match(r'arn:aws:sns:(rma|lpg)::at-.+', arn)
        sns.delete_topic(TopicArn=arn)

    api.request("DELETE", url)
