import atexit
import os
import pytest
import re
import socket
import time
import urllib

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from constants import NUMBERS
from constants import REPEATED_WHITE_SPACE
from constants import RESOURCE_CREATION_CONCURRENCY_LIMIT
from constants import RESOURCE_NAME_PREFIX
from constants import RUNTIME_PATH
from constants import SERVER_START_TIMEOUT
from contextlib import closing
from contextlib import contextmanager
from contextlib import suppress
from datetime import datetime, timedelta
from dns import reversename
from dns.resolver import NXDOMAIN
from dns.resolver import Resolver
from errors import Timeout
from functools import cached_property, lru_cache
from hashlib import blake2b
from ipaddress import ip_address
from ipaddress import ip_network
from itertools import chain
from paramiko import SSHClient, AutoAddPolicy
from paramiko.ssh_exception import ChannelException
from paramiko.ssh_exception import NoValidConnectionsError
from paramiko.ssh_exception import SSHException
from pathlib import Path
from psutil import Process
from requests.exceptions import HTTPError
from testinfra.backend.paramiko import ParamikoBackend
from types import SimpleNamespace
from typing import Generator
from uuid import uuid4
from warnings import warn


@lru_cache(maxsize=1)
def global_run_id():
    """ Determines the test-run id of each process. Though there is such a
    variable in pytest-xdist, it is not available if run without it, and it
    is not available early in the execution.

    The run id is a combination of the pyest process's pid and creation time,
    as well as a timestamp of when the test was started.

    Since this has to work across worker processes, the run id is stored in
    the runtime path, under the id of the runner process.

    """
    proc = pytest_process()

    proc_id = f'{proc.pid}-{proc.create_time()}'
    proc_id = blake2b(proc_id.encode('utf-8'), digest_size=8).hexdigest()

    path = Path(f'{RUNTIME_PATH}/at-{proc_id}.runid')

    if path.exists():
        with path.open('r') as f:
            return f.read()

    with path.open('w') as f:
        timestamp = datetime.now().isoformat(timespec="milliseconds")
        run_id = f'at-{timestamp}-{proc_id}'

        # To ensure files written with the run_id in them can be uploaded as
        # GitHub Action artifacts, we cannot use colons in it, since that is
        # a character that is not allowed.
        run_id = run_id.replace(':', '-')

        f.write(run_id)

    atexit.register(path.unlink)

    return run_id


def pytest_process(current_pid=None):
    """ Returns the top-most pytest process, which may or may not be
    controlling workers.

    """
    pid = current_pid or os.getpid()
    process = Process(pid)
    command = ' '.join(process.cmdline())

    # Test runner
    if 'py.test' in command or 'pytest' in command:
        return process

    # Worker process
    if 'python' in command:
        return pytest_process(current_pid=process.ppid())

    raise RuntimeError("Not inside a py.test run")


def is_matching_slug(image, fuzzy_slugs):
    """ Returns True if the given image matches any of the given slugs.

    Those slugs are checked fuzzily using 'in'. They do not need to match
    exactly.

    """

    for fuzzy_slug in fuzzy_slugs:
        if fuzzy_slug in image['slug']:
            return True

    return False


def is_present_in_zone(image, zone_slug):
    """ Returns True if the given image is present in the given zone. """

    for zone in image['zones']:
        if zone['slug'] == zone_slug:
            return True

    return False


def generate_server_name(request, original_name=''):
    """ Generates a name using the given prefix and suffix. """

    # By default, include the name of the test in the server name
    if request.scope != 'session':
        scope = request.node.name
    else:
        scope = 'session'

    # Include a per-test run prefix and add an optionally chosen name as suffix
    name = f'{RESOURCE_NAME_PREFIX}-{scope}-{original_name or ""}'.lower()

    # Replace everything that is not allowed in a hostname by a -
    name = re.sub(r'[^a-z0-9-\.]', '-', name)

    # Squeeze repeated -
    name = re.sub(r'-{2,}', '-', name)

    # Truncate name to 63 characters, but keep the caller supplied name. This
    # part might be important to distinguish different servers in a test
    if len(name) > 63:
        name = f'{name[:63-len(original_name)-1]}-{original_name.lower()}'

    # Remove - at the start or end
    name = name.strip('-')

    return name


def in_parallel(factory, instances=None, count=None):
    """ Runs the given function in parallel with the given parameters.

    The canoncial usage should illustrate what this is all about:

        s1, s2 = in_parallel(create_server, instances=(
            {'name': 'server-1', 'image': 'debian-8'},
            {'name': 'server-2', 'image': 'debian-8'},
        ))

    Or if the function doesn't take any arguments:

        s1, s2 = in_parallel(some_function, count=2)

    """

    def create(instance):
        if isinstance(instance, dict):
            return factory(**instance)

        if isinstance(instance, (list, tuple, Generator)):
            return factory(*instance)

        return factory(instance)

    # Require instances or a count, disallow both together
    assert instances or count
    assert None in (instances, count)

    if count:
        instances = [{}] * count

    max_workers = RESOURCE_CREATION_CONCURRENCY_LIMIT

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        return tuple(pool.map(create, instances))


def oneliner(text, shrink=True):
    """ Takes the given text (a shell oneliner) put it on one line.

    This allows us to write shell oneliners in a more readable way, so instead
    of writing something like this:

        cmd = (
            "ffmpeg -loop_input -i cover.jpg -i soundtrack.mp3 -shortest "
            "-acodec copy output_video.mp4"
        )

    We can write something like this without having to worry about white-space:

        cmd = oneliner('''
            ffmpeg -loop_input
                   -i cover.jpg
                   -i soundtrack.mp3
                   -shortest
                   -acodec copy

                output_video.mp4
        ''')

    The resulting oneliner has all newlines removed, each line stripped and
    all repeated whitespace replaced with single space (so it looks good in
    a log or process table).

    If repeated whitespace should be preserved, set `shrink` to False.

    """
    line = ' '.join(s.strip() for s in text.splitlines())

    if shrink:
        line = REPEATED_WHITE_SPACE.sub(' ', line)

    return line.strip()


class FaultTolerantParamikoBackend(ParamikoBackend):
    """ Overrides the ParamikoBackend of testinfra with a version that is
    better equipped to deal with suddenly disconnected SSH connections.

    If there's an issue with the SSH connection, we retry for up to three
    seconds. The default Paramiko backend does this as well, but it only
    retries a single time.

    Additionally, this backend is initialised with a connection factory,
    instead of a set of configuration parameters.

    """

    def __init__(self, client_factory, retries=3):
        super().__init__('paramiko://')
        self.client_factory = client_factory
        self.retries = 3

    @cached_property
    def client(self):
        return self.client_factory()

    def connect(self):
        self.client

    def disconnect(self):
        self.__dict__.pop('client', None)

    def run(self, command, *args, **kwargs):
        last_error = None

        for _ in range(0, self.retries):
            try:
                return super().run(command, *args, **kwargs)
            except (SSHException, NoValidConnectionsError, TimeoutError) as e:
                last_error = e
                self.disconnect()
                time.sleep(1)

        raise last_error


def host_connect_factory(ip, username, ssh_key, deadline, jump_host=None):
    """ Returns a function that connects to the host when called.

    * If the connection fails, it should be retried.
    * The result of the connect function is a connected paramiko client.

    """

    client = SSHClient()
    client.set_missing_host_key_policy(AutoAddPolicy())

    channel = None

    def connect():
        nonlocal channel

        if jump_host and (channel is None or channel.closed):
            channel = open_jump_host_channel(ip, jump_host, deadline)

        try:
            client.connect(
                hostname=str(ip),
                username=username,
                pkey=ssh_key,
                sock=channel,
            )
        except Exception:
            if channel is not None:
                channel.close()

            raise

        return client

    return connect


def open_jump_host_channel(private_ip, jump_host, deadline):
    """ Returns a channel to the jump-host through which Paramiko can connect
    to a host with a private IP.

    """

    transport = jump_host.host.backend.client.get_transport()

    while datetime.now() < deadline:
        time.sleep(1)

        with suppress(ChannelException, EOFError, SSHException):
            return transport.open_channel(
                'direct-tcpip', (str(private_ip), 22), ('', 0))

    raise Timeout(
        f'Connecting to the server from the jump-host took '
        f'longer than {SERVER_START_TIMEOUT}s'
    )


def extract_number(text):
    """ Extracts the first consecutive number in a text.

    If no number can be extracted, an error is raised.

    Examples:
        "123 a 456" will return 123 (int)
        "foo 123.12" will return 123.12 (float)

    """

    match = NUMBERS.search(text)

    if not match:
        raise RuntimeError(f"Could not find number in '{text}'")

    number = match.group()

    if '.' in number:
        return float(number)

    return int(number)


def matches_attributes(obj, **attributes):
    """ Returns True if the given object has all the given attribute values.

    For example:

        >>> class Foo(object):
        >>>    a = 1
        >>>    b = 2

        >>> matches_attributes(Foo(), a=1, b=2)
        True

        >>> matches_attributes(Foo(), a=1)
        True

        >>> matches_attributes(Foo(), a=2)
        False

    """

    for k, v in attributes.items():
        if getattr(obj, k) != v:
            return False

    return True


def retry_for(seconds, exceptions=(AssertionError, ), pause=1):
    """ Allows to retry functions for a while, causing either exceptions or
    warnings.

    Example:

        def connect_to_server():
            ...

        retry_for(seconds=5).or_fail(connect_to_server, msg="No connection")

    The function itself returns a `Retryable` instance, which offers multiple
    ways to deal with retries. Either an exception is used (`or_fail`) or
    a warning (`or_warn`).

    By default there's a 1 second pause between retries and only assertions
    are caught. This can be changed however by passing a list of exceptions
    and a pause in seconds to the `retry_for` function.

    The `or_*` functions take an optional message to be used for the warning
    or exception. The function that is called as part of the retry logic
    does not support any arguments.

    """

    return Retryable(seconds, exceptions, pause)


class Retryable(object):
    """ Retries functions for a given time.

    See `retry_for` for documentation and usage.

    """

    def __init__(self, seconds, exceptions, pause):
        self.seconds = seconds
        self.pause = pause
        self.exceptions = exceptions

    def or_fail(self, fn, msg=None, *args, **kwargs):
        timeout = datetime.utcnow() + timedelta(seconds=self.seconds)

        while datetime.utcnow() < timeout:
            try:
                fn(*args, **kwargs)
            except self.exceptions as e:
                last_exception = e
            else:
                return

            time.sleep(self.pause)

        msg = msg or f"Function {fn} failed after {self.seconds}s of trying"

        raise Timeout(msg) from last_exception

    def or_warn(self, fn, msg=None, *args, **kwargs):
        try:
            self.or_fail(fn, msg=msg, *args, **kwargs)
        except Timeout as e:
            warn(e)


def arguments_as_namespace(fn, args, kwargs):
    """ Inspects functions signature and, given args and kwargs, returns a
    dictionary of all passed parameters, wheter passed as keyword arguments,
    or not.

    See https://stackoverflow.com/a/40363565/138103

    """
    names = fn.__code__.co_varnames[:fn.__code__.co_argcount]
    return SimpleNamespace(**dict(zip(names, args)), **kwargs)


def yield_lines(path, tail=True):
    """ Yields lines from the given file forever. When a call to read a line
    does not find anything, None is returned (the file is kept open, so a
    later call might return more lines).

    """

    with open(path, 'r') as f:

        if tail:
            f.seek(0, os.SEEK_END)

        while True:
            line = f.readline()

            if not line:
                yield None

            yield line


def dot_access(path, obj):
    """ Accesses the attributes of the given object using dot notation.

    For example:

    >>> dot_access('foo.bar', {'foo': {'bar': 1}})
    1

    Unlike more sophisticated approaches, like JMESPath, this function only
    does dot access, but supports dicts and objects alike.

    """

    dots = list(reversed(path.split('.')))

    while dots:
        dot = dots.pop()

        try:
            obj = obj[dot]
        except (KeyError, TypeError):
            obj = getattr(obj, dot)

    return obj


def raw_headers(url, method="GET"):
    """ Returns the headers of the given URL as a dictionary, where each
    key is a field name (as RFC 2616 calls them, aka header key), and each
    value ist a list of headers.

    Field names are titleized ('content-language' becomes 'Content-Language').
    Values are untouched.

    Take the following headers as example:

        vary: Accept-Encoding
        vary: Accept-Encoding
        allow: GET, HEAD, OPTIONS

    This results in:

        {
            'Vary': ['Accept-Encoding', 'Accept-Encoding'],
            'Allow': ['GET, HEAD, OPTIONS'],
        }

    """

    # Use urllib.request instead of requests as requests already sanitizes the
    # response headers by combining duplicate header field names and makes it
    # impossible to check for invalid or unwanted header configurations.
    request = urllib.request.Request(url=url, method=method)
    headers = urllib.request.urlopen(request).getheaders()

    result = defaultdict(list)

    for field_name, field_value in headers:
        result[field_name].append(field_value)

    return result


def is_port_online(host, port, timeout=1.0):
    """ Returns true if the given TCP port is online. """

    # Support server resources
    if hasattr(host, 'ip'):
        host = str(host.ip('public', 4))

    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.settimeout(timeout)

        try:
            return sock.connect_ex((host, port)) == 0
        except socket.gaierror:
            return False


def reverse_ptr(address, ns):
    """ Queries the given nameserver for the PTR record of an IP. """

    resolver = Resolver(configure=False)
    resolver.nameservers.append(socket.gethostbyname(ns))

    reverse = reversename.from_address(str(address))

    try:
        return str(resolver.resolve(reverse, 'PTR')[0])
    except NXDOMAIN:
        return None


def nameservers(zone):
    """ Returns the nameservers associated with a given zone. """

    resolver = Resolver(configure=True)
    return [str(s) for s in resolver.resolve(zone, 'NS')]


def is_public(address):
    """ Returns True if we consider the given address to be public.

    This is not an equivalent to the `is_global` flag used by ipaddress. Here
    we consider IPs from the CGNAT space (RFC6598) to be public, since we
    use that address range as a replacement for public IPs internally.

    For IPv6 this function behaves exactly like `is_global`.

    """

    address = ip_address(address)

    if address.version == 6:
        return address.is_global

    return address.is_global or address in ip_network('100.64.0.0/10')


def build_http_url(ip, path='/', port=None, ssl=False):
    """ Construct a HTTP URL for a server reachable on an IP address. """

    method = 'https' if ssl else 'http'

    port = f':{port}' if port else ''

    if ip_address(ip).version == 6:
        ip = f'[{ip}]'

    return f'{method}://{ip}{port}{path}'


def start_persistent_download(prober, load_balancer, backends, name='wget'):
    """ Start a persistent download which does not end until the connection or
    the process is terminated.

    This uses a special URL path on the lbaas-http-test-server which sends
    random data in an endless loop.
    """

    uuid = uuid4()
    prober.run(oneliner(f'''
        systemd-run --user --unit {name}
        wget
        --output-document /dev/null
        --connect-timeout 5
        --tries 1
        {build_http_url(load_balancer.vip(4), f"/endless/{uuid}")}
    '''))

    def check_backends():
        backends_hit = get_backends_for_request(
            backends,
            f'/endless/{uuid}',
            200,
        )
        # Assert exactly one backend is returned
        assert len(backends_hit) == 1

    # The request sometimes only appears in the log after a few seconds
    retry_for(seconds=10).or_fail(
        check_backends,
        msg='Could not find backend for request within 10s.',
    )

    return get_backends_for_request(backends, f'/endless/{uuid}', 200)[0]


def get_backends_for_request(backends, url='/', status_code=200):
    """ Returns all backends which have received requests for an URL. """

    def check_backend(backend):
        return backend.run(oneliner(f'''
            journalctl
            --user-unit lbaas-http-test-server
            | grep '"GET {url} HTTP/1.1" {status_code} -'
        ''')).succeeded

    return list(filter(check_backend, backends))


def setup_lbaas_http_test_server(backend, ssl=False):
    """ Upload the HTTP test server to the backend and start it. """

    # Create some content indetifying the server
    backend.run('echo "Backend server running on $(hostname)." > index.html')

    if ssl:
        # Crete a SSL server certificate and private key
        backend.run(oneliner('''
            openssl req
            -new
            -x509
            -keyout server.pem
            -out server.pem
            -days 365
            -nodes
            -batch
        '''))

    # Copy test server Python script to backend server
    backend.put_file('scripts/lbaas-http-test-server')
    backend.run('chmod +x lbaas-http-test-server')

    # Run the backend server as a transient systemd service
    backend.run(oneliner(f'''
        systemd-run
        --user
        --unit lbaas-http-test-server
        ./lbaas-http-test-server {"--ssl" if ssl else ""}
    '''))


def setup_lbaas_udp_test_server(backend):
    """ Start a simple UDP server that returns a static response. """
    msg = f'Backend server running on {backend.name} {backend.uuid}'

    # Run the UDP echo server as a transient systemd service
    # UDP echo server with 'pipes' option to prevent race condition where echo
    # exits before socat writes UDP data to stdin (avoiding intermittent
    # broken pipe errors)
    backend.run(oneliner(f'''
        systemd-run
        --user
        --unit lbaas-udp-echo-server
        socat -v UDP4-RECVFROM:8000,fork SYSTEM:"echo {msg}.",pipes
    '''))


def setup_lbaas_backend(backend, backend_network, ssl=False, protocol='tcp'):
    """ Configures a server to work as an LBaaS test HTTP backend.

    The server is plugged into the backend network and a simple HTTP test
    server is started.

    Optionally SSL is setup for the HTTP test server.
    """

    # Plug the backend server into the backend network
    interfaces = [{'network': iface['network']['uuid']}
                  for iface in backend.interfaces]
    interfaces.append({'network': backend_network.uuid})

    backend.update(interfaces=interfaces)
    backend.enable_dhcp_in_networkd(backend.interfaces[-1])

    # Setup the HTTP test server
    if protocol in ('tcp', 'proxy', 'proxyv2'):
        setup_lbaas_http_test_server(backend, ssl)
    elif protocol == 'udp':
        setup_lbaas_udp_test_server(backend)
    else:
        raise AssertionError(f'Unknown protocol: {protocol}')


def wait_for_url_ready(url, prober, content=None, timeout=90):
    """ Waits for an URL to return an OK status code or specific content. """

    def verify_content():
        # Note: insecure=False means don't verify certificates. The
        # certificates on the LB test setup won't validate.
        output = prober.http_get(url, insecure=True)
        if content:
            assert content == output

    # Wait for LB to become operational
    retry_for(seconds=timeout).or_fail(
        verify_content,
        msg=f'URL {url} was not ready within {timeout}s.',
    )


def wait_for_udp_response(ip, prober, timeout):
    def assert_udp_response():
        assert 'Backend server' in prober.udp_get(ip, 80)

    retry_for(seconds=timeout).or_fail(
        assert_udp_response,
        msg=f'Assertion not met within {timeout}s.'
    )


def wait_for_load_balancer_ready(load_balancer, prober, port=None, ssl=False,
                                 protocol='tcp', timeout=90, content=None,
                                 ip_version=4):
    """ Waits for the load balancer to become operational. """

    if port is None:
        port = 443 if ssl else 80

    # Wait for the load balancer to be running.
    load_balancer.wait_for('running', seconds=120)

    # Wait for the LB to serve content
    if protocol == 'tcp':
        wait_for_url_ready(
            build_http_url(load_balancer.vip(ip_version), port=port, ssl=ssl),
            prober,
            content,
            timeout,
        )
    elif protocol == 'udp':
        wait_for_udp_response(load_balancer.vip(ip_version), prober, timeout)
    else:
        raise AssertionError(f'Unknown protocol: {protocol}')


def unique(iterable):
    """ Returns a set of unique values from an interable object (eg. list) """

    return set(iterable)


@contextmanager
def assert_takes_no_longer_than(seconds):
    """ Asserts that inside of the "with" block takes no longer than the
    given amount of time.

    """
    start = time.monotonic()
    yield
    took = time.monotonic() - start
    assert took <= seconds, f"{took}s > {seconds}s"


def extract_short_error(longrepr):
    """ Takes a longrepr exception report and extracts the most relevant
    line from it.

    Since the last exception is the first one to have occurred, and the '>'
    points to the executed line, followed by error lines, we take the first
    error line after the '>'.

    """

    if not longrepr:
        return None

    prev = None

    # Start from the bottom since that's nearer
    for line in reversed(longrepr.splitlines()):
        if line.startswith('E'):
            prev = line
        if line.startswith('>'):
            break

    return prev and prev[1:].strip() or prev


@contextmanager
def skip_test_when(match, reason=None):
    """ Wraps a block of code and skips the test with the given reason, if
    an exception occurs whose string contains the given "match".

    By default, if no reason is given, it equals the match string.

    If there is a match, pytest.skip raises an exception that causes pytest
    to show the test as skipped. If there is none, the exception is re-raised
    as-is.

    """

    reason = reason or match

    try:
        yield
    except HTTPError as e:
        if match in str(e) or match in e.response.text:
            pytest.skip(reason)

        raise

    except Exception as e:
        if match in str(e):
            pytest.skip(reason)

        raise


def flatten(list_of_lists):
    """ Flatten one level of nesting. """

    return list(chain.from_iterable(list_of_lists))
