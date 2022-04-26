import os
import pytest
import random
import re
import requests
import secrets

from api import API
from constants import API_URL
from constants import LOCKS_PATH
from constants import RUNNER_ID
from datetime import datetime
from datetime import timedelta
from events import trigger
from itertools import combinations
from paramiko import ECDSAKey
from pathlib import Path
from resources import CustomImage
from resources import FloatingIP
from resources import Network
from resources import Server
from resources import ServerGroup
from resources import Volume
from util import global_run_id
from util import in_parallel
from util import is_matching_slug
from util import is_present_in_zone
from xdist import is_xdist_master
from xdist import is_xdist_worker

# The available zones
ZONES = (
    'rma1',
    'lpg1',
)

# The following images are considered common and are tested more thoroughly
COMMON = (
    'debian',
    'ubuntu',
    'centos',
)

# The following images are excluded from automatic testing
EXCLUDE = (
    'opnsense',
    'pfsense',
)

# Function names containing this expression are tested with all/common images
generatable_fn = re.compile(r'_(?P<kind>all|common)_images($|_)')


def pytest_addoption(parser):
    """ Additional CLI options for pytest """

    parser.addoption(
        '--ssh-key',
        action='append',
        default=[],
        help='Additional SSH public key file to inject into test servers',
    )

    parser.addoption(
        '--default-image',
        action='store',
        default='debian-10',
        help='Default image slug to use for tests',
    )

    parser.addoption(
        '--default-image-only',
        action='store_true',
        default=False,
        help='Only test the default image',
    )

    parser.addoption(
        '--exclude-image',
        action='append',
        default=[],
        help="Exclude images matching the given slug from tests",
    )

    parser.addoption(
        '--zone',
        action='store',
        default=random.choice(ZONES),
        choices=ZONES,
        help="Zone to run the tests in (defaults to a random zone)",
    )

    parser.addoption(
        '--username',
        action='store',
        default=None,
        help="Username used for custom images"
    )


def pytest_sessionstart(session):
    """ Processes the options and caches them for later use. """

    zone = session.config.option.zone
    api = API(scope='session', zone=zone, read_only=True)

    # Request the available images via REST
    images = api.get('/images').json()

    # Allow the manual selection of custom images, by pretending those are
    # normal images (they are otherwise skipped).
    for custom in api.get('/custom-images').json():
        images.append({
            'slug': f'custom:{custom["slug"]}',
            'name': custom["name"],
            'zones': custom["zones"],
        })

    default_image = session.config.option.default_image
    default_only = session.config.option.default_image_only

    # The default image is matched exactly, because there can only be one
    default = next(i for i in images if i['slug'] == default_image)

    # Additionally excluded images
    exclude = session.config.option.exclude_image

    if default_only:
        images = [default]
        common = [default]
    else:

        # Exclude custom images
        images = [i for i in images if not i['slug'].startswith('custom:')]

        # Exclude certain images
        images = [i for i in images if not is_matching_slug(i, EXCLUDE)]

        # Exclude all manually excluded images
        images = [i for i in images if not is_matching_slug(i, exclude)]

        # Include only images present in the given zone
        images = [i for i in images if is_present_in_zone(i, zone)]

        # Select common images
        common = [i for i in images if is_matching_slug(i, COMMON)]

    session.config.option.all_images = images
    session.config.option.common_images = common
    session.config.option.default_image = default

    if is_xdist_master(session):
        os.environ['PYTEST_XDIST_MASTER'] = '1'

    if is_xdist_worker(session):
        # This has to be set explicitly, as the environment is inherited from
        # the master, where PYTEST_XDIST_MASTER is set to 1.
        os.environ['PYTEST_XDIST_MASTER'] = '0'

        return

    # Announce the start of a test-run.
    trigger(event='run.start', run_id=global_run_id())

    # Cleanup what other tests may have left behind, if they got killed
    API(scope=None, zone=zone, read_only=False).cleanup(
        limit_to_scope=False, limit_to_process=False)


def pytest_sessionfinish(session, exitstatus):
    """ Clear up any remaining resources. """

    if is_xdist_worker(session):
        return

    # Cleanup what pytest-xdist workers may have left behind
    zone = session.config.option.zone

    API(scope=None, zone=zone, read_only=False).cleanup(
        limit_to_scope=False, limit_to_process=False)

    # Announce the end of a test-run.
    trigger(
        event='run.end',
        result=exitstatus == 0 and 'success' or 'failure',
        run_id=global_run_id()
    )

    # Remove older locks (removing all might break concurrent test runs).
    horizon = datetime.now() - timedelta(hours=12)

    for file in Path(LOCKS_PATH).glob('*.lock'):
        if datetime.fromtimestamp(file.stat().st_mtime) < horizon:
            file.unlink()


def pytest_generate_tests(metafunc):
    """ Automatically generate the 'image' fixture for tests requesting it.


    If the test or fixture simply includes the 'image' fixture, the default
    image is used. If the test or fixture contains any of the following
    strings in the name, the fixture is automatically parameterise to include
    all or common images:

    * all_images
    * common_images

    For example, a test written as follows, will be called once for each
    common image:

        def test_common_images_have_systemd(create_server, image):
            server = create_server(image=image)
            server.assert_run('command -v systemctl')

    This could be paramterised directly on the fixture, but this way we can
    dynamically control the list of servers through CLI parameters.

    For example, we can limit all tests to a single image as follows:

        py.test --default-image centos-8 --default-image-only

    """

    function_name = metafunc.function.__name__

    if 'image' not in metafunc.fixturenames:

        # If the function was named according to the schema, but the image
        # parameter was not used, we should fail. It is easy to make that
        # mistake which would lead to wrong assumptions
        assert 'all_images' not in function_name
        assert 'common_images' not in function_name

        return

    match = generatable_fn.search(function_name)

    if match:
        attrib = f'{match.group("kind")}_images'  # all_images, common_images
        images = getattr(metafunc.config.option, attrib)
    else:
        images = [metafunc.config.option.default_image]

    metafunc.parametrize(
        'image', images, scope='session', ids=lambda i: i['slug']
    )


def pytest_report_header(config, startdir):
    """ Announces test parameters in the session header. """

    # Announce the API parameters
    print("api:", API_URL)
    print("runner-id:", RUNNER_ID)
    print("zone:", config.option.zone)

    # Announce the selected images
    images = config.option.all_images
    common = config.option.common_images
    default = config.option.default_image

    other = [i for i in images if i not in common]

    if config.option.default_image_only:
        print("image:", default['name'])
    else:
        print("default image:", default['name'])

    if common != [default]:
        print("common images:", ', '.join(i['name'] for i in common))

    if other:
        print("other images:", ', '.join(i['name'] for i in other))


def pytest_collection_modifyitems(session, config, items):
    """ Sort the tests by filename, test-name, and id. """

    def sort_key(item):
        return item.fspath, item.name

    items.sort(key=sort_key)


def pytest_runtest_logstart(nodeid, location):
    """ Announce the name of the test, before any fixtures are loaded. """

    # Masters get logs from workers, which we don't want to trigger on.
    if os.environ.get('PYTEST_XDIST_MASTER') == '1':
        return

    trigger('test.start', name=nodeid)


def pytest_runtest_logreport(report):
    """ Announce various steps a single tests passes. """

    # Masters get logs from workers, which we don't want to trigger on.
    if os.environ.get('PYTEST_XDIST_MASTER') == '1':
        return

    if report.when == 'setup':
        trigger('test.setup', name=report.nodeid, outcome=report.outcome)
    elif report.when == 'call':
        trigger('test.call', name=report.nodeid, outcome=report.outcome)
    elif report.when == 'teardown':
        trigger('test.teardown', name=report.nodeid, outcome=report.outcome)
    else:
        raise NotImplementedError(f"Unsupported report stage: {report.when}")


@pytest.fixture(scope='session')
def random_ssh_key():
    """ A random SSH key used to communicate with launched servers. """

    yield ECDSAKey.generate()


@pytest.fixture(scope='session')
def public_key(random_ssh_key):
    """ The public part of the random SSH key in base64. """

    yield f'ecdsa-sha2-nistp256 {random_ssh_key.get_base64()}'


@pytest.fixture(scope='session')
def zone(request):
    return request.config.option.zone


@pytest.fixture(scope='session')
def region(zone):
    return zone.rstrip('0123456789')


@pytest.fixture(scope='session', autouse=True)
def all_public_keys(request, public_key):
    """ A list of all public keys used on the server.

    This includes the randomly generated key, as well as any additonal SSH keys
    passed via `--ssh-key`.

    This fixture is autoused because it is necessary for all new servers. We
    might otherwise lazy load it during parallel creation of servers which
    would result in an error.

    """
    result = [public_key]

    for path in request.config.option.ssh_key:
        with open(path, 'r') as f:
            result.append(f.read().strip())

    return result


@pytest.fixture(scope='session', autouse=True)
def session_api(request):
    """ An API instances whose resources are cleaned up after each session.

    Note: autouse is set to True, as this ensures that these fixtures are
    created before - and deleted after - manually requested fixtures.

    """

    zone = request.session.config.option.zone
    api = API(scope='session', zone=zone, read_only=False)

    yield api

    api.cleanup(limit_to_process=True, limit_to_scope=True)


@pytest.fixture(scope='function', autouse=True)
def function_api(request):
    """ An API instances whose resources are cleaned up after each test.

    Note: autouse is set to True, as this ensures that these fixtures are
    created before - and deleted after - manually requested fixtures.

    """

    zone = request.session.config.option.zone
    api = API(scope='function', zone=zone, read_only=False)

    yield api

    api.cleanup(limit_to_process=True, limit_to_scope=True)


@pytest.fixture(scope='function')
def create_server(request, function_api, image):
    """ Factory to launch function scoped VMs. """

    return Server.factory(
        request=request,
        api=function_api,
        image=image['slug']
    )


@pytest.fixture(scope='session')
def create_server_for_session(request, session_api):
    """ Factory to launch session scoped VMs. """

    return Server.factory(
        request=request,
        api=session_api
    )


@pytest.fixture(scope='session')
def prober(create_server_for_session):
    """ Server acting as a jump-host for servers without public IP address. """

    return create_server_for_session(
        image='debian-10', use_private_network=True)


@pytest.fixture(scope='function')
def server(create_server, image):
    """ Simple small default server with only public networking (v4 and v6).

    """
    return create_server(image=image['slug'])


@pytest.fixture(scope='function')
def server_with_private_net(create_server, image):
    """ Default server with private network. """

    return create_server(image=image['slug'], use_private_network=True)


@pytest.fixture(scope='function')
def two_servers_in_same_subnet(create_server, image):
    """ Tries to find two servers in the same subnet.

    This is not straight-forward as we have no way of requesting two servers
    to be in the same subnet. However, we have a limited number of subnets
    and can therefore most likely find a solution within a few tries.

    """

    def network_id(server):
        targets = (i for i in server.interfaces if i['type'] == 'public')
        return next(i['network']['uuid'] for i in targets)

    def two_in_same_subnet(servers):
        for a, b in combinations(servers, 2):
            if network_id(a) == network_id(b):
                return a, b

        return None, None

    for _ in range(4):

        servers = in_parallel(create_server, instances=(
            {'name': 's1', 'image': image['slug']},
            {'name': 's2', 'image': image['slug']},
            {'name': 's3', 'image': image['slug']},
            {'name': 's4', 'image': image['slug']},
        ))

        a, b = two_in_same_subnet(servers)

        for s in servers:
            if s is not a and s is not b:
                s.delete()

        if a and b:
            return a, b

    raise RuntimeError("Failed to find two servers in the same subnet")


@pytest.fixture(scope='function')
def create_floating_ip(request, function_api, region):
    """ Factory to launch function scoped Floating IPs. """

    return FloatingIP.factory(
        request=request,
        api=function_api,
        region=region
    )


@pytest.fixture(scope='function')
def floating_ipv4(create_floating_ip):
    """ Floating IPv4 address. """

    return create_floating_ip(ip_version=4)


@pytest.fixture(scope='function')
def floating_ipv6(create_floating_ip):
    """ Floating IPv6 address. """

    return create_floating_ip(ip_version=6)


@pytest.fixture(params=[4, 6], ids=['IPv4', 'IPv6'], scope='function')
def floating_ip(request, create_floating_ip):
    """ Parameterised Floating IPs for v4 and v6. """

    return create_floating_ip(ip_version=request.param)


@pytest.fixture(scope='function')
def floating_network(create_floating_ip):
    """ A floating network (IPv6 only), with a /56 prefix. """

    return create_floating_ip(ip_version=6, prefix_length=56)


@pytest.fixture(scope='function')
def create_volume(request, function_api, zone):
    """ Factory to launch function scoped volumes. """

    return Volume.factory(
        request=request,
        api=function_api,
        zone=zone
    )


@pytest.fixture(scope='function')
def ssd_volume(create_volume):
    """ Additional SSD volume. """

    return create_volume(size=50, volume_type='ssd')


@pytest.fixture(scope='function')
def bulk_volume(create_volume):
    """ Additional bulk volume. """

    return create_volume(size=100, volume_type='bulk')


@pytest.fixture(params=['ssd', 'bulk'], scope='function')
def volume(request, create_volume):
    """ Parameterised volume for SSD and bulk. """

    return create_volume(size=100, volume_type=request.param)


@pytest.fixture(scope='function')
def create_server_group(request, function_api, zone):
    """ Factory to launch function scoped server groups. """

    return ServerGroup.factory(
        request=request,
        api=function_api,
        zone=zone,
        name=f'test-group-{secrets.token_hex(8)}'
    )


@pytest.fixture(scope='function')
def server_group(create_server_group):
    """ Function scoped server group. """

    return create_server_group()


@pytest.fixture(scope='function')
def create_private_network(request, function_api, zone):
    """ Factory to launch function scoped private networks. """

    return Network.factory(
        request=request,
        api=function_api,
        zone=zone,
        name=f'test-{secrets.token_hex(8)}',
        auto_create_ipv4_subnet=False
    )


@pytest.fixture(scope='function')
def private_network(create_private_network):
    """ A function scoped private network. """

    return create_private_network()


@pytest.fixture(scope='session', params=['raw', 'qcow2'])
def custom_alpine_image(request, session_api, zone):
    """ A session scoped custom Alpine image. """

    fmt = request.param
    img = 'https://at-images.objects.lpg.cloudscale.ch/alpine'
    url = f'{img}.{fmt}'

    # All images are expanded to raw and then hashed, so the hash is not
    # per-format, but always refers to raw.
    md5 = requests.get(f'{img}.md5').text
    sha256 = requests.get(f'{img}.sha256').text

    image = CustomImage(
        request=request,
        api=session_api,
        zones=(zone, ),
        name='Alpine',
        slug=f'custom-{secrets.token_hex(8)}',
        url=url,
        source_format=fmt,
        user_data_handling='extend-cloud-config',
    )

    image.create()

    if image.progress['status'] == 'failed':
        raise RuntimeError(f"Failed to import {url}")

    if image.checksums['md5'] != md5:
        raise RuntimeError(f"Wrong MD5 for {url}: {md5}")

    if image.checksums['sha256'] != sha256:
        raise RuntimeError(f"Wrong SHA-256 for {url}: {sha256}")

    yield image
