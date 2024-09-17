import secrets
import textwrap
import tempfile
import time

from constants import IMAGE_SPECIFIC_USER_DATA
from contextlib import suppress
from datetime import datetime
from datetime import timedelta
from errors import ServerError
from errors import Timeout
from events import with_trigger
from functools import lru_cache
from hashlib import blake2b
from ipaddress import ip_address
from ipaddress import ip_interface
from ipaddress import ip_network
from pathlib import Path
from testinfra.host import Host
from util import build_http_url
from util import FaultTolerantParamikoBackend
from util import generate_server_name
from util import host_connect_factory
from util import in_parallel
from util import is_port_online
from util import is_public
from util import matches_attributes
from util import oneliner
from util import RESOURCE_NAME_PREFIX
from util import SERVER_START_TIMEOUT
from uuid import uuid4


class CloudscaleResource:
    """ A cloudscale.ch resource like a Server or a Floating IP. """

    def __init__(self, request, api):
        self.info = {}
        self.spec = {}
        self.request = request
        self.api = api

    def __getattr__(self, name):

        if 'info' in self.__dict__:
            if name in self.info:
                return self.info[name]

        if 'spec' in self.__dict__:
            if name in self.spec:
                return self.spec[name]

        raise AttributeError(f'Attribute does not exist: {name}')

    @classmethod
    def factory(cls, **defaults):
        """ Returns a factory that creates the resource using the given
        parameters, pre-filled with the given defaults.

        For example:

            new_focal_server = Server.factory(image='ubuntu-20.04')
            server = new_focal_server(flavor='flex-4-1')

        """
        def create_resource_factory(**parameters):
            resource = cls(**{**defaults, **parameters})
            resource.create()

            return resource

        return create_resource_factory

    @property
    def created(self):
        return 'href' in self.info

    def create(self):
        """ This needs to be implemented by the subclass. """
        raise NotImplementedError()

    def refresh(self):
        self.info = self.api.get(self.href).json()

    def wait_for(self, status, seconds=60):
        timeout = datetime.now() + timedelta(seconds=seconds)
        self.wait_until(status, timeout=timeout)

    @with_trigger('resource.wait')
    def wait_until(self, status, timeout=None):

        timeout = timeout or self.default_timeout()
        seconds = (timeout - datetime.now()).total_seconds()

        negate = status.startswith('!')
        status = negate and status[1:] or status

        while datetime.now() < timeout:
            self.refresh()

            if negate:
                if self.status != status:
                    break
            else:
                if self.status == status:
                    break

            # Don't check this too eagerly, to keep log output less noisy
            time.sleep(2.5)
        else:
            raise Timeout(f"Waited more than {seconds}s for '{status}' status")

    def delete(self):
        if not self.created:
            return

        self.api.delete(self.href)
        self.info = {}


class Server(CloudscaleResource):
    """ Provides servers which are cleaned up after the tests ran through. """

    def __init__(self, request, api, jump_host=None, auto_name=True, **spec):
        super().__init__(request, api)

        # the username to use as a fallback for custom images
        self.username = spec.pop('username', request.config.option.username)

        self.jump_host = jump_host

        self.spec = self.default_spec()
        self.spec.update(spec)

        # If interfaces are defined, all is manual
        if 'interfaces' in self.spec:
            self.spec.pop('use_public_network', None)
            self.spec.pop('use_private_network', None)

        # automatically extract the slug if the image was given as dict
        if isinstance(self.spec['image'], dict):
            self.spec['image'] = self.spec['image']['slug']

        # Warn if jump-host is missing
        if not self.has_public_interface and jump_host is None:
            raise ServerError(
                "Can't connect to a server without public network if there "
                "is no jump-host."
            )

        # get a unique server name, unless explicitly disabled
        if auto_name:
            name = generate_server_name(request, spec.get('name', ''))
        else:
            name = spec['name']

        # The name is limited to 63 characters
        self.spec.update({'name': name[:63].strip('-')})

    def default_spec(self):
        spec = {
            'flavor': 'flex-4-1',
            'image': self.request.config.option.default_image['slug'],
            'zone': self.request.config.option.zone,
            'volume_size_gb': 10,
            'use_public_network': True,
            'use_ipv6': True,
            'ssh_keys': self.request.getfixturevalue('all_public_keys'),
        }

        user_data = self.image_specific_user_data(spec['image'])

        if user_data:
            spec['user_data'] = user_data

        return spec

    def image_specific_user_data(self, image_slug):
        for expression, user_data in IMAGE_SPECIFIC_USER_DATA.items():
            if expression.match(image_slug):
                return user_data

    def default_timeout(self, seconds=SERVER_START_TIMEOUT):
        return datetime.now() + timedelta(seconds=seconds)

    @with_trigger('server.create')
    def create(self):
        self.info = self.api.post('/servers', json=self.spec).json()
        self.wait_for('running', seconds=SERVER_START_TIMEOUT)
        self.connect()

    def wait_for_http_content(self, host, content, seconds, port=80):
        """ Reads the http response of the host until the expected
        content is returned.

        Fails if the return code is not 200 or the timeout expires.

        """

        # The host could be a host name or an IP address (native or string)
        address = str(host)

        # IPv6 addresses need square brackets
        if address.startswith(('http://', 'https://')):
            url = address
        else:
            if ':' in address:
                url = f'http://[{address}]'
            else:
                url = f'http://{address}'

        timeout = datetime.now() + timedelta(seconds=seconds)

        while datetime.now() < timeout:
            if self.output_of(f'wget -q -O - {url}').strip() == content:
                return

            time.sleep(1)

        raise Timeout(f"Waited more than {seconds}s for '{content}' at {url}")

    def http_get(self, url, insecure=False):
        """ Runs curl or wget (whatever is available) and returns the body. """

        if self.run('command -v curl').exit_status == 0:
            insecure = '--insecure' if insecure else ''
            return self.output_of(oneliner(f'''
                curl
                --silent
                --location
                --connect-timeout 5
                {insecure}
                {url}
            '''))

        if self.run('command -v wget').exit_status == 0:
            insecure = '--no-check-certificate' if insecure else ''
            return self.output_of(oneliner(f'''
                wget
                --quiet
                --output-document -
                --connect-timeout 5
                --tries 1
                {insecure}
                {url}
            '''))

        raise NotImplementedError("No suitable HTTP client found")

    def create_host(self, timeout):
        """ Creates the testinfra host.

        See <https://testinfra.readthedocs.io>.

        """

        # Prepare the connection to the host
        connect = host_connect_factory(
            ip=self.ip(self.jump_host and 'private' or 'public', 4),
            username=self.image.get('default_username') or self.username,
            ssh_key=self.request.getfixturevalue('random_ssh_key'),
            deadline=timeout,
            jump_host=self.jump_host,
        )

        # Wait until we can connect
        while datetime.now() < timeout:
            time.sleep(1)

            try:
                host = Host(FaultTolerantParamikoBackend(connect))
                host.backend.connect()
            except KeyboardInterrupt:
                raise
            except Exception as e:
                # As a catch-all this might also fail on programming errors,
                # but it is very hard to limit the exceptions otherwise,
                # because before the connection is stable we may see a host
                # of exceptions that are subject to changes outside our
                # control (i.e. paramiko, testinfra)
                last_exception = e
            else:
                break
        else:
            raise Timeout(
                f'Connection to {self.name} timed out '
                f'after {SERVER_START_TIMEOUT}s'
            ) from last_exception

        # The host is now ready to use
        self.host = host

        # Wait for cloud-init to finish
        has_cloud_init = self.run('command -v cloud-init').exit_status == 0

        if has_cloud_init:
            self.wait_for_cloud_init(host, timeout)

        # Validate IPv6 if necessary
        if self.spec['use_ipv6'] and self.has_public_interface:
            self.wait_for_non_tentative_ipv6()
            self.wait_for_ipv6_default_route()

            # By default, `ndisc_notify` is turned off in most Linux
            # distributions, which means the server does not send an
            # unsolicited neighbor advertisement.
            #
            # As a result, the switch does not learn that neighbor right after
            # the interface comes up.
            #
            # To ensure that the L3 switches have valid neighbor entries for
            # the IPv6 global unicast address we ping a designated IPv6
            # address. A DNS lookup is used for it, to not hard-code the
            # address. The DNS lookup is usually done via IPv4 on the host.
            ipv6_address = self.resolve('api.cloudscale.ch', version=6)[0]
            self.ping(ipv6_address, tries=2, wait=5)

    @with_trigger('server.wait-for-cloud-init')
    def wait_for_cloud_init(self, host, timeout):
        while datetime.now() < timeout:

            if host.file('/var/lib/cloud/instance/boot-finished').exists:
                break

            time.sleep(0.5)
        else:
            raise Timeout(
                f'Connection to {self.name} timed out '
                f'after {SERVER_START_TIMEOUT}s'
            )

    @with_trigger('server.wait-for-non-tentative-ipv6')
    def wait_for_non_tentative_ipv6(self, timeout=60):
        address = str(self.ip('public', 6))
        until = datetime.utcnow() + timedelta(seconds=timeout)

        while datetime.utcnow() <= until:
            preferred = self.output_of('sudo ip a | grep -v tentative')

            if address in preferred:
                return

            time.sleep(1)

        raise Timeout('Wait for non-tentative IPv6 timed-out')

    @with_trigger('server.wait-for-ipv6-default-route')
    def wait_for_ipv6_default_route(self, timeout=30):
        until = datetime.utcnow() + timedelta(seconds=timeout)

        while datetime.utcnow() <= until:
            ipv6_routes = self.output_of('sudo ip -6 route')

            if 'default via fe80::1 dev' in ipv6_routes:
                return

            time.sleep(1)

        raise Timeout('Wait for default IPv6 route timed-out')

    @with_trigger('server.wait-for-port')
    def wait_for_port(self, port, state, timeout=30):
        """ Waits for the given port to be open. Note that this connects from
        the current host to the server:

            [Acceptance Tests] → [Server]

        So this is *not* a valid way to connect from a prober to a server
        in a private network.

        """

        until = datetime.utcnow() + timedelta(seconds=timeout)

        while datetime.utcnow() <= until:
            is_online = is_port_online(self, port)

            if state == 'online' and is_online:
                return

            if state == 'offline' and not is_online:
                return

            time.sleep(0.5)

        raise Timeout(f"Timed out waiting for {self}:{port} to be {state}")

    @with_trigger('server.update')
    def update(self, **properties):
        """ Updates the given properties of the server, using the
        /v1/servers/<uuid> PATCH call.

        """

        self.api.patch(self.href, json=properties)
        self.wait_for(status='!changing')

    def action(self, name, expected_status):
        """ Runs given action and waits for the expected status. """

        self.api.post(f'{self.href}/{name}')

        # Wait briefly for an initial 'changing' state after the action.
        #
        # This is necessary for some actions like 'reboot', where the state
        # before the action is the same as the state after the action.
        with suppress(Timeout):
            self.wait_for('changing', seconds=15)

        self.wait_for(expected_status)

    @with_trigger('server.stop')
    def stop(self):
        self.action('stop', expected_status='stopped')

    @with_trigger('server.start')
    def start(self):
        self.action('start', expected_status='running')
        self.connect()

    @with_trigger('server.reboot')
    def reboot(self):
        self.action('reboot', expected_status='running')
        self.connect()

    @with_trigger('server.connect')
    def connect(self):
        self.create_host(self.default_timeout())

    @lru_cache(maxsize=2)
    def ping_command(self, ip_version):
        """ Returns the ping command to use. Some distributions only support
        'ping -6', some only support 'ping6'. For IPv4 there are no such
        differences.

        """
        if ip_version == 4:
            return 'ping'
        else:
            if self.run('command -v ping6').exit_status == 0:
                return 'ping6'
            else:
                return 'ping -6'

    def ping(self, address, interval=1, count=1, wait=1, timeout=None,
             fragment=None, size=56, tries=1, expect_failure=False):
        """ Pings the given address and raises an assertion if that fails.

        Optionally, a number of tries can be specified.

        To ping once and fail if that does not work:

            server.ping(address)

        To ping 10 times in 1 second (no loss may occur):

            server.ping(address, count=10, interval=0.1)

        To wait 10 seconds for a host to come back:

            server.ping(address, count=1, wait=1, tries=10)

        To invert the check (expect the ping to fail):

            server.ping(address, expect_failure=True)

        The return value is the output of the ping command.

        """

        if isinstance(address, FloatingIP):
            address = address.ip

        if fragment is None:
            pmtu_discovery = None
        elif fragment is True:
            pmtu_discovery = 'want'
        else:
            pmtu_discovery = 'do'

        arguments = (
            ('-i', interval),
            ('-c', count),
            ('-w', timeout),
            ('-W', wait),
            ('-s', size),
            ('-M', pmtu_discovery)
        )

        ping = self.ping_command(ip_address(address).version)
        opts = ' '.join(f'{o} {v}' for o, v in arguments if v is not None)

        if expect_failure:
            check = 'failed'
            error = f'ping succeeded unexpectedly on {self.name}'
        else:
            check = 'succeeded'
            error = f'ping failed on {self.name}'

        for n in range(1, tries + 1):
            start = time.monotonic()
            cmd = self.run(f'{ping} {opts} {address}')

            if getattr(cmd, check):
                return cmd.stdout

            # The wait argument in this function is generally used to define an
            # upper bound a ping check should take by the tests that call it.
            #
            # For example: ping(ip, wait=1, tries=10) is meant to ping an IP
            # 10 times, for a total of up to 10 seconds.
            #
            # Ping's "-W" parameter does not work like that however. Depending
            # on the scenario it won't wait at all. For example, if the
            # network is unreachable, ping returns immediately.
            #
            # To correct for that, we add extra sleep to ensure that a ping
            # "failure" causes the function to take as long as intended
            # by the caller.
            time.sleep(max(wait - (time.monotonic() - start), 0))

        raise AssertionError(f"{error}: {cmd.stdout}, {cmd.stderr}")

    def resolve(self, name, version):
        """ Resolve the given name, returing the IPv4 or IPv6 addresses
        associated with it.

        """

        if version == 4:
            command = f'getent ahostsv4 {name}'
        else:
            command = f'getent ahostsv6 {name}'

        addrs = (a for a in self.output_of(command).splitlines())
        addrs = (a.strip().split(' ')[0] for a in addrs)
        addrs = (a for a in addrs if a)

        return tuple(addrs)

    def identify_by_ssh_host_key(self, ip, servers=None):
        """ Gets the SSH host key of the given address and compares it to the
        servers found in the API. If there is a match, the server dict is
        returned.

        """
        if hasattr(ip, 'address'):
            address = ip.address
        else:
            address = ip

        key = self.output_of(
            f'ssh-keyscan -t ed25519 {address} | cut -d " " -f 2-3')

        if not key:
            return None

        for server in (servers or self.api.resources('/servers')):
            if key in (server.get('ssh_host_keys') or ()):
                return server

        return None

    def reachable_via_ip(self, *ips, timeout=None):
        """ Tries to connect to the given IPs (in parallel) and returns True
        if all given IPs point to this server.

        Uses SSH host keys to determine the identity of the server.

        """
        servers = self.api.resources('/servers')
        until = timeout and time.monotonic() + timeout or None

        while True:
            identities = in_parallel(
                lambda ip: self.identify_by_ssh_host_key(ip, servers), ips)

            for server in identities:
                if self.uuid != server['uuid']:
                    break
                else:
                    return True

            if timeout is None or time.monotonic() > until:
                return False

    def file_path_exists(self, path):
        """ Returns true if the given path exists. """

        return self.host.file(path).exists

    @with_trigger('server.output-of')
    def output_of(self, command):
        """ Returns the output of the given command. This is a slightly
        shorter, and a bit more readable version of alias of Testinfra's
        check_output function.

        """
        return self.host.check_output(command)

    @with_trigger('server.run')
    def run(self, command):
        """ Alias for self.host.run. Unlike `assert_run`, this function does
        not raise an Assertion if the command failed.

        """
        return self.host.run(command)

    @with_trigger('server.assert-run')
    def assert_run(self, command, valid_exit_codes=(0,)):
        """ Alias for self.host.run_expect. Unlike `run`, this function raises
        an Assertion if the command failed.
        """
        return self.host.run_expect(valid_exit_codes, command)

    @property
    def has_public_interface(self):
        """ Returns true if the server is expected to be reachable through
        a public IP due to its initial configuration.

        If it is indeed reachable is another question.

        """

        # If interfaces are given, they take precedence over other variables
        if 'interfaces' in self.spec:
            for interface in self.spec['interfaces']:
                if interface['network'] == 'public':
                    return True

            return False

        if self.spec.get('use_public_network', True):
            return True

        return False

    def nth_interface_name(self, n):
        """ Returns the interface name of the nth interface. """
        paths = self.output_of('find /sys/class/net -type l').splitlines()

        names = (p.rsplit('/', 1)[-1] for p in paths)
        names = (n for n in names if n != 'lo')
        names = list(names)

        names.sort()

        return names[n]

    @property
    def public_interface(self):
        """ Returns the host specification of the first public interface. """

        return self.host.interface(self.nth_interface_name(0))

    @property
    def private_interface(self):
        """ Returns the host specification of the first private interface. """

        return self.host.interface(self.nth_interface_name(1))

    def ip_address_config(self, iface_type, ip_version, network=None):
        for interface in self.interfaces:
            if network and not interface['network']['uuid'] == network:
                continue

            for address in interface['addresses']:
                if interface['type'] != iface_type:
                    continue

                if address['version'] != ip_version:
                    continue

                return address

        # No address of this type found
        return None

    def ip(self, iface_type, ip_version, fail_if_missing=True, network=None):
        """ Get IP address from the given interface type and version.

        If `fail_if_missing` is set to False, None may be returned.

        """
        config = self.ip_address_config(iface_type, ip_version, network)

        if config:
            return ip_address(config['address'])
        elif fail_if_missing:
            raise AssertionError(f"No IP address: {iface_type}/{ip_version}")
        else:
            return None

    def gateway(self, iface_type, ip_version, fail_if_missing=True):
        """ Get the gateway IP addr from the given interface type and version.

        If `fail_if_missing` is set to False, None may be returned.

        """
        config = self.ip_address_config(iface_type, ip_version)

        if config and config['gateway']:
            return ip_address(config['gateway'])
        elif fail_if_missing:
            raise AssertionError(f"No gateway: {iface_type}/{ip_version}")
        else:
            return None

    def interface_name(self, floating_ip):
        """ Generates a unique interface name for the given Floating IP.

        Since this might have to deal with IPv6, we create a hash to ensure
        that the interface name length is less than 16.

        """
        address = str(floating_ip).encode('utf-8')
        digest = blake2b(address, digest_size=6).hexdigest()

        return f'f-{digest}'

    def dhcp_reply(self, interface_name, ip_version, timeout=2.5):
        """ Starts a DHCP discovery on the interface and returns a reply,
        without configuring the interface.

        This is used to assert DHCP replies and requires dhclient to be
        installed.

        """
        return self.output_of(oneliner(f"""
            sudo timeout {timeout}s dhclient {interface_name}
                -{ip_version}
                -lf /dev/stdout
                -n -d -q -1
                --no-pid
            2>/dev/null || true
        """))

    def configure_floating_ip(self, floating_ip):
        interface = self.interface_name(floating_ip)

        self.assert_run(oneliner(f"""
            sudo ip link add {interface} type dummy &&
            sudo ip addr add {floating_ip} dev {interface}
        """))

    def unconfigure_floating_ip(self, floating_ip):
        interface = self.interface_name(floating_ip)

        self.assert_run(f'sudo ip link delete {interface}')

    def configured_ip_addresses(self, **attributes):
        """ Returns all IP addresses configured on any of the interfaces. """

        interfaces = self.output_of('ls /sys/class/net').split()
        interfaces = (self.host.interface(i) for i in interfaces)

        matches = []

        if 'is_public' in attributes:
            if attributes.pop('is_public'):
                include_ip = is_public
            else:
                include_ip = lambda ip: not is_public(ip)  # noqa
        else:
            include_ip = lambda ip: True  # noqa

        for interface in interfaces:
            for address in interface.addresses:
                address = ip_address(address)

                if matches_attributes(address, **attributes):
                    if include_ip(address):
                        matches.append(address)

        return matches

    @with_trigger('server.scale-root')
    def scale_root_disk(self, new_size):
        """ Scales the root disk of the server. """

        volume_uuid = self.volumes[0]['uuid']
        self.api.patch(f'/volumes/{volume_uuid}', json={'size_gb': new_size})

    def assigned_memory(self):
        """ Returns the memory, in GiB that is assigned to the server.

        We are talking about assigned memory here, because this is the memory
        that the kernel encounters at boot. It is not quite equal to the total
        memory available to the user, since some of the total memory is used by
        the kernel.

        Ideally we would read this from dmidecode, where we would get the
        exact number. However, not every image has dmidecode installed.

        The next best thing is the number of present pages from /proc/zoneinfo,
        which is the number of pages the kernel can see.

        That is still slightly off from the actual physical memory, but it's
        close enough.

        """

        # The page size on all of our images is 4KiB
        page_size = 4096

        pages = self.output_of('grep present /proc/zoneinfo').splitlines()
        pages = (int(p.strip().split(' ', 1)[-1]) for p in pages)

        return round(sum(pages) * page_size / 1024 / 1024 / 1024)

    def assigned_cpus(self):
        """ Returns the number of vCPUs assigned to the server. """

        return int(self.output_of("nproc"))

    def fs_size(self, device):
        """ Returns the size of the filesystem, using the appropriate
        filesystem tools.

        """

        fs = self.output_of(f'df --output=fstype {device} | tail -n 1')

        if fs.startswith('ext'):
            block_size = int(self.output_of(oneliner(f"""
                sudo dumpe2fs -h {device}
                | grep "Block size:"
                | cut -d ":" -f 2
            """)))
            block_count = int(self.output_of(oneliner(f"""
                sudo dumpe2fs -h {device}
                | grep "Block count:"
                | cut -d ":" -f 2
            """)))

            return block_size * block_count

        if fs == 'xfs':
            block_size = int(self.output_of(oneliner(f"""
                sudo xfs_info {device}
                | grep --extended-regexp '^data'
                | grep --extended-regexp --only-matching 'bsize=[0-9]+'
                | cut -d "=" -f 2
            """)))

            block_count = int(self.output_of(oneliner(f"""
                sudo xfs_info {device}
                | grep --extended-regexp '^data'
                | grep --extended-regexp --only-matching 'blocks=[0-9]+'
                | cut -d "=" -f 2
            """)))

            return block_size * block_count

        raise NotImplementedError(f"Unsupported filesystem: {fs}")

    def put_file(self, filename, remote_filename=None, sudo=False):
        if not remote_filename:
            remote_filename = Path(filename).name

        sftp = self.host.backend.client.open_sftp()

        if not sudo:
            sftp.put(filename, remote_filename)
        else:
            temp_filename = f'/tmp/scp-{secrets.token_hex(16)}'
            sftp.put(filename, temp_filename)
            self.assert_run(f'sudo mv {temp_filename} {remote_filename}')

    def put_file_content(self, remote_filename, content, sudo=False):
        with tempfile.NamedTemporaryFile('w') as f:
            f.write(content)
            f.flush()

            self.put_file(f.name, remote_filename, sudo)

    def enable_dhcp_in_networkd(self, interface):
        """ Additional private network interfaces have to be explicitly
        configured to use DHCP, to get an IP address.

        """

        name = interface['mac_address'].replace(':', '-')

        self.put_file_content(
            f"/etc/systemd/network/{name}.network",
            textwrap.dedent(f"""\
                [Match]
                MACAddress={interface['mac_address']}

                [Network]
                DHCP=yes
            """),
            sudo=True,
        )

        # Restart systemd-networkd to apply the changes
        self.assert_run("sudo systemctl restart systemd-networkd")

        # Wait a few seconds for the DHCP to be applied
        time.sleep(5)


class FloatingIP(CloudscaleResource):

    def __init__(self, request, api, ip_version, region, prefix_length=None,
                 server=None, reverse_ptr=None):
        super().__init__(request, api)

        self.spec = {
            'ip_version': ip_version,
            'region': region,
        }

        if prefix_length:
            self.spec['prefix_length'] = prefix_length

        if server:
            self.spec['server'] = server

        if reverse_ptr:
            self.spec['reverse_ptr'] = reverse_ptr

    def __str__(self):
        return str(self.ip)

    @with_trigger('floating-ip.create')
    def create(self):
        self.info = self.api.post('/floating-ips', json=self.spec).json()

    @with_trigger('floating-ip.assign')
    def assign(self, server=None, load_balancer=None):
        assert not (server and load_balancer), \
            "Can't assign a Floating IP to a server and a load balancer at " \
            "the same time"

        if server:
            json = {'server': server.uuid}
        elif load_balancer:
            json = {'load_balancer': load_balancer.uuid}
        else:
            raise AssertionError(
                'The cloudscale.ch API does not support unassiging '
                'a Floating IP.'
            )

        self.api.patch(
            self.href,
            json=json,
        )
        self.refresh()

    @with_trigger('floating-ip.update')
    def update(self, **properties):
        """ Updates the given properties of the Floating IP, using the
        /v1/floating-ip/<network-id> PATCH call.

        """

        self.api.patch(self.href, json=properties)

    @property
    def ip(self):
        return ip_interface(self.network).ip

    @property
    def address(self):
        return self.ip

    @property
    def version(self):
        return self.ip.version

    @property
    def network(self):
        return ip_network(self.info['network'])


class Volume(CloudscaleResource):

    def __init__(self, request, api, size, zone, volume_type='ssd'):
        super().__init__(request, api)

        self.spec = {
            'name': f'{RESOURCE_NAME_PREFIX}-{uuid4().hex[:8]}',
            'size_gb': size,
            'type': volume_type,
            'zone': zone
        }

    @with_trigger('volume.create')
    def create(self):
        self.info = self.api.post('/volumes', json=self.spec).json()

    def update(self, **properties):
        self.api.patch(self.href, json=properties)
        self.refresh()

    @with_trigger('volume.attach')
    def attach(self, server):
        self.update(server_uuids=[server.uuid])

    @with_trigger('volume.scale')
    def scale(self, new_size):
        self.update(size_gb=new_size)

    @with_trigger('volume.detach')
    def detach(self):
        self.update(server_uuids=[])


class ServerGroup(CloudscaleResource):

    def __init__(self, request, api, name, zone):
        super().__init__(request, api)

        self.spec = {
            'name': f'{RESOURCE_NAME_PREFIX}-{name}',
            'type': 'anti-affinity',
            'zone': zone
        }

    @with_trigger('server-group.create')
    def create(self):
        self.info = self.api.post('/server-groups', json=self.spec).json()

    @with_trigger('server-group.rename')
    def rename(self, name):
        self.api.patch(self.href, json={'name': name})
        self.refresh()


class Network(CloudscaleResource):

    def __init__(self, request, api, name, zone, auto_create_ipv4_subnet):
        super().__init__(request, api)

        self.spec = {
            'name': f'{RESOURCE_NAME_PREFIX}-{name}',
            'zone': zone,
            'auto_create_ipv4_subnet': auto_create_ipv4_subnet,
        }

    @with_trigger('network.create')
    def create(self):
        self.info = self.api.post('/networks', json=self.spec).json()

    def add_subnet(self, cidr, gateway_address=None, dns_servers=None):
        subnet = Subnet(
            request=self.request,
            api=self.api,
            network=self,
            cidr=cidr,
            gateway_address=gateway_address,
            dns_servers=dns_servers,
        )

        subnet.create()

        return subnet

    @with_trigger('network.change-mtu')
    def change_mtu(self, mtu):
        self.api.patch(self.href, json={'mtu': mtu})


class Subnet(CloudscaleResource):

    def __init__(self, request, api, network, cidr,
                 gateway_address, dns_servers):
        super().__init__(request, api)

        self.spec = {
            'network': network.uuid,
            'cidr': cidr,
            'gateway_address': gateway_address,
            'dns_servers': dns_servers,
        }

    def __contains__(self, address):
        return ip_address(address) in ip_network(self.cidr)

    @with_trigger('subnet.create')
    def create(self):
        self.info = self.api.post('/subnets', json=self.spec).json()

    @with_trigger('subnet.change-dns-servers')
    def change_dns_servers(self, dns_servers):
        self.api.patch(self.href, json={'dns_servers': dns_servers})

    def delete(self):
        """ Subnets are not explicitly deleted as they are automatically
        removed when their network is removed.

        """
        pass


class CustomImage(CloudscaleResource):
    """ The CustomImage resource is special. It is fed with the parameters
    used for the custom-images/import call and waits until the image has been
    imported.

    This means the critical path in create is rather long, which is unfortunate
    if you want to cancel the test early, but it ensures that we do not leave
    unfinished imports behind.

    """

    def __init__(self, request, api, **spec):
        super().__init__(request, api)
        self.spec = spec

    @with_trigger('custom-image.import')
    def create(self):
        self.progress = self.api.post(
            'custom-images/import', json=self.spec).json()

        self.wait_for_completion()

        self.info = self.api.get(self.progress["custom_image"]["href"]).json()

    def wait_for_completion(self, seconds=120):
        timeout = datetime.now() + timedelta(seconds=seconds)

        while datetime.now() < timeout:
            current_status = self.progress['status']
            if current_status == 'in_progress':
                time.sleep(1)
                self.progress = self.api.get(self.progress['href']).json()

                continue
            if current_status == 'success':

                return

            raise RuntimeError(f"Custom Image Import has unexpected status: "
                               f"{current_status}")
        raise Timeout(f"Waited more than {seconds}s for {self.url}")


class LoadBalancer(CloudscaleResource):

    def __init__(self, request, api, name, zone, flavor='lb-standard',
                 vip_addresses=None):

        super().__init__(request, api)
        self.spec = {
            'name': generate_server_name(request, name),
            'zone': zone,
            'flavor': flavor,
        }
        if vip_addresses:
            self.spec['vip_addresses'] = vip_addresses

        # Initialize lists of subobjects
        self.listeners = []
        self.pools = []
        self.pool_members = []
        self.health_monitors = []

    def refresh(self):
        super().refresh()

        # Update all "subresources"
        for kind in ('listeners', 'pools', 'pool_members', 'health_monitors'):
            setattr(self, kind, [self.api.get(i['href']).json()
                                 for i in getattr(self, kind)])

    def vip_address_config(self, ip_version):
        for address in self.vip_addresses:
            if address['version'] != ip_version:
                continue

            return address

        # No address of this type found
        return None

    def vip(self, ip_version, fail_if_missing=True):
        """ Get VIP address from the given IP version.

        If `fail_if_missing` is set to False, None may be returned.

        """
        config = self.vip_address_config(ip_version)

        if config:
            return ip_address(config['address'])
        elif fail_if_missing:
            raise AssertionError(f"No IPv{ip_version} address.")
        else:
            return None

    @with_trigger('load-balancer.create')
    def create(self):
        self.info = self.api.post('load-balancers', json=self.spec).json()

    @with_trigger('load-balancer.add-pool')
    def add_pool(self, name, algorithm, protocol='tcp'):
        self.pools.append(self.api.post(
            'load-balancers/pools',
            json={
                'name': f'{RESOURCE_NAME_PREFIX}-pool-{name}',
                'load_balancer': self.uuid,
                'algorithm': algorithm,
                'protocol': protocol,
            }).json())
        return self.pools[-1]

    @with_trigger('load-balancer.add-pool-member')
    def add_pool_member(self, pool, backend, backend_network):

        private_iface = backend.ip_address_config('private', 4,
                                                  backend_network.uuid)

        self.pool_members.append(self.api.post(
            f'load-balancers/pools/{pool["uuid"]}/members',
            json={
                'name': f'{RESOURCE_NAME_PREFIX}-pool-member-'
                        f'{backend.name}',
                'protocol_port': 8000,
                'address': private_iface['address'],
                'subnet': private_iface['subnet']['uuid'],
            }).json())
        return self.pool_members[-1]

    @with_trigger('load-balancer.remove-pool-member')
    def remove_pool_member(self, pool, member):
        self.api.delete(
            f'load-balancers/pools/{pool["uuid"]}/members/{member["uuid"]}',
        )

        self.pool_members = list(filter(lambda x: x['uuid'] != member["uuid"],
                                        self.pool_members))

    @with_trigger('load-balancer.disable-pool-member')
    def toggle_pool_member(self, pool, member, enabled=True):
        self.api.patch(
            f'load-balancers/pools/{pool["uuid"]}/members/{member["uuid"]}',
            json={'enabled': enabled},
        )

    @with_trigger('load-balancer.add-listener')
    def add_listener(self, pool, protocol_port, allowed_cidrs=None, name=None,
                     protocol='tcp'):

        if name is None:
            name = f'port-{protocol_port}'

        self.listeners.append(self.api.post(
            'load-balancers/listeners',
            json={
                'name': f'{RESOURCE_NAME_PREFIX}-listener-{name}',
                'pool': pool['uuid'],
                'protocol': 'tcp',
                'protocol_port': protocol_port,
                'allowed_cidrs': allowed_cidrs or [],
            }).json())
        return self.listeners[-1]

    @with_trigger('load-balancer.update-listener')
    def update_listener(self, listener, **kwargs):
        self.api.patch(
            f'load-balancers/listeners/{listener["uuid"]}',
            json=kwargs,
        )

    @with_trigger('load-balancer.add-health-monitor')
    def add_health_monitor(self, pool, monitor_type, monitor_http_config):
        self.health_monitors.append(self.api.post(
            'load-balancers/health-monitors',
            json={
                'pool': pool['uuid'],
                'type': monitor_type,
                'http': monitor_http_config,
            }).json())
        return self.health_monitors[-1]

    def build_url(self, url='/', addr_family=4, port=None, ssl=False):
        """ Build a URL to fetch content from a load balancer """
        return build_http_url(self.vip(addr_family), url, port, ssl)

    def verify_backend(self, prober, backend, count=1, port=None):
        """ Verify the next count requests go to the given backend server. """

        for i in range(count):
            assert (prober.http_get(self.build_url(url='/hostname', port=port))
                    == backend.name)
