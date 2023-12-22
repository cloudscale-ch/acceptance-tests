import requests

from constants import API_TOKEN
from constants import API_URL
from constants import LOCKS_PATH
from constants import PROCESS_ID
from constants import RUNNER_ID
from events import trigger
from filelock import FileLock
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry


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

        # DELETE may fail on resources when they are being created, so we
        # retry those a number of times
        retry_strategy = Retry(
            total=5,
            status_forcelist=[400],
            allowed_methods=["DELETE"],
            backoff_factor=1
        )

        adapter = CloudscaleHTTPAdapter(
            max_retries=retry_strategy,
        )

        self.mount("https://", adapter)

    def post(self, url, data=None, json=None, **kwargs):
        assert not data, "Please only use json, not data"

        if json:
            json['tags'] = {
                'runner': RUNNER_ID,
                'process': PROCESS_ID,
                'scope': self.scope,
            }

        return super().post(url, data=data, json=json, **kwargs)

    def on_response(self, response, *args, **kwargs):
        trigger('request.after', request=response.request, response=response)

        try:
            response.raise_for_status()
        except requests.exceptions.HTTPError as e:
            ignore = e.request.method == 'DELETE' \
                and e.response.status_code == 404

            if not ignore:
                print(f"{e.request.method} {e.request.url} failed:")
                print(e.response.text)

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

        yield from resources('/servers')
        yield from resources('/load-balancers')
        yield from resources('/volumes')
        yield from resources('/floating-ips')
        yield from resources('/subnets')
        yield from resources('/networks')
        yield from resources('/server-groups')
        yield from resources('/custom-images')

    def cleanup(self, limit_to_scope=True, limit_to_process=True):
        """ Deletes resources created by this API object. """

        for r in self.runner_resources():
            assert r['tags']['runner'] == RUNNER_ID

            if limit_to_scope and r['tags']['scope'] != self.scope:
                continue

            if limit_to_process and r['tags']['process'] != PROCESS_ID:
                continue

            self.delete(r['href'])
