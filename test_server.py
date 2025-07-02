"""

Server Functionality
====================

The cloudscale.ch API can be used to automate actions otherwise available
through the web-interface. Most importantly, servers can be launched and scaled
at any time.

API Docs: https://www.cloudscale.ch/en/api/v1

"""
import secrets
import textwrap

from util import extract_number
from util import oneliner
from util import skip_test_when


def test_change_flavor_from_flex_to_flex(create_server):
    """ It is possible to change from one flex flavor to another. """

    # Start a server with the flex-4-1 flavor
    server = create_server(flavor='flex-4-1')

    assert server.assigned_memory() == 4
    assert server.assigned_cpus() == 1

    # To change the flavor we need to stop the server first
    server.stop()

    # Change the flavor to flex-8-2
    server.update(flavor='flex-8-2')

    # Make sure the server has been scaled
    server.start()

    assert server.assigned_memory() == 8
    assert server.assigned_cpus() == 2


def test_change_flavor_from_flex_to_plus(create_server):
    """ It is possible to change from a flex to a plus flavor. """

    # Start a server with the flex-4-1 flavor
    server = create_server(flavor='flex-4-1')

    assert server.assigned_memory() == 4
    assert server.assigned_cpus() == 1

    # To change the flavor we need to stop the server first
    server.stop()

    # Change the flavor to plus-8-2
    server.update(flavor='plus-8-2')

    # Make sure the server has been scaled
    server.start()

    assert server.assigned_memory() == 8
    assert server.assigned_cpus() == 2


def test_change_flavor_from_plus_to_flex(create_server):
    """ It is possible to change from a plus to a flex flavor. """

    # Start a server with the plus-8-2 flavor
    server = create_server(flavor='plus-8-2')

    assert server.assigned_memory() == 8
    assert server.assigned_cpus() == 2

    # To change the flavor we need to stop the server first
    server.stop()

    # Change the flavor to flex-4-1
    server.update(flavor='flex-4-1')

    # Make sure the server has been scaled
    server.start()

    assert server.assigned_memory() == 4
    assert server.assigned_cpus() == 1


def test_change_flavor_from_plus_to_plus(create_server):
    """ It is possible to change from one plus flavor to another. """

    # Start a server with the plus-8-2 flavor
    server = create_server(flavor='plus-8-2')

    assert server.assigned_memory() == 8
    assert server.assigned_cpus() == 2

    # To change the flavor we need to stop the server first
    server.stop()

    # Change the flavor to plus-12-3
    server.update(flavor='plus-12-3')

    # Make sure the server has been scaled
    server.start()

    assert server.assigned_memory() == 12
    assert server.assigned_cpus() == 3


def test_hostname(create_server):
    """ Servers can be named on creation, with some restrctions.

    During creation, the name must only contain letters (a-z), digits (0-9),
    hyphens (-) and dots (.). The server's hostname in the guest VM is set
    to this name.

    """

    # Servers can be created using a fully qualified domain name
    server = create_server(name='node-1.example.org', auto_name=False)
    assert server.output_of('hostname --fqdn') == 'node-1.example.org'

    # Servers can be also be created using a simple name
    server = create_server(name='node-1', auto_name=False)
    assert server.output_of('hostname --fqdn') == 'node-1'


def test_rename_server(server):
    """ Servers can be renamed at any time, with few restrictions.

    After creation, the name can be any 1-255 characters long. Unicode is
    supported. The server's hostname in the guest VM is not changed.

    """

    # Server names can be chosen quite freely
    server.update(name='hal-9000.example.org')
    assert server.name == 'hal-9000.example.org'

    # Up to 255 characters are allowed
    server.update(name='0' * 255)
    assert len(server.name) == 255

    # Feel free to use special characters
    server.update(name='🤖-host')
    assert server.name == '🤖-host'

    server.update(name='acme | cluster nodes | master')
    assert server.name == 'acme | cluster nodes | master'


def test_reboot_server(server):
    """ Servers can be rebooted using the API or through the shell. """

    # Get the time of the last boot
    boot_timestamp = server.output_of('uptime --since')

    # Reboot the server through the API (automatically reconnects)
    server.reboot()

    # Make sure that the reboot happened
    assert server.output_of('uptime --since') != boot_timestamp

    # Update the time of the last boot
    boot_timestamp = server.output_of('uptime --since')

    # Try to reboot through the shell
    server.run('sudo systemctl reboot')

    # Wait for SSH to be unavailable, or we might re-connect prematurely
    server.wait_for_port(22, 'offline', timeout=10)

    # Wait for the server to finish rebooting
    server.connect()

    # Make sure that this reboot happened as well
    assert server.output_of('uptime --since') != boot_timestamp


def test_stop_and_start_server(server):
    """ Servers can be stopped using the API or through the shell. """

    # Get the time of the last boot
    boot_timestamp = server.output_of('uptime --since')

    # Stop the server through the API, then start it
    server.stop()
    server.start()

    # Make sure that the reboot happened
    server.output_of('uptime --since') != boot_timestamp

    # Update the time of the last boot
    boot_timestamp = server.output_of('uptime --since')

    # Try to stop the server through the shell
    server.run('sudo systemctl poweroff')

    # Wait for the server to fully stop
    server.wait_for(status='stopped')

    # Start it using the API
    server.start()

    # Make sure the server was started
    assert server.output_of('uptime --since') != boot_timestamp


def test_rename_server_group(server_group):
    """ Server groups can be renamed freely. """

    # Change the name of the server group
    server_group.rename('frontend-servers')

    # Make sure the name has been set
    assert server_group.name == 'frontend-servers'


def test_no_cpu_steal_on_plus_flavor(create_server, image):
    """ Plus flavor servers have dedicated CPU cores, which means other tenants
    cannot cause CPU steal as they might with shared CPU cores.

    Note that due to an implementation detail, CPU steal of up to 1% may be
    observed.

    """

    # Create a Plus-8-2 instance
    server = create_server(image=image, flavor='plus-8-2')

    # We need a stress tool to saturate our cores
    server.assert_run('sudo apt update --allow-releaseinfo-change ')
    server.assert_run('sudo apt install -y stress')

    # Run stress in the background, on all cores
    server.assert_run('sudo systemd-run stress --cpu 2')

    # Observe CPU steal for 30 seconds
    steal = server.output_of(oneliner("""
        top -n 60 -d 0.5 -b
        | egrep '^%Cpu'
        | egrep -o '[0-9.]+ st'
    """))

    max_steal = max(extract_number(line) for line in steal.splitlines())

    # Make sure the CPU steal does not exceed 1%
    assert max_steal <= 1


def test_random_number_generator(server):
    """ Our servers come with a paravirtual random number generator.

    See https://www.cloudscale.ch/en/news/2020/03/09/entropy-random-numbers.

    """

    # Make sure the 'rdrand' CPU feature is enabled
    server.assert_run('grep -q rdrand /proc/cpuinfo')

    # Ensure that we can also see the hwrng virtio device
    path = '/sys/devices/virtual/misc/hw_random/rng_available'
    server.assert_run(f'grep -q virtio_rng {path}')


def test_metadata_on_all_images(server):
    """ All servers have access to metadata through a link-local IP address and
    a read-only config drive.

    See https://docs.openstack.org/nova/latest/user/metadata.html

    """

    # The config drive is usually available as /dev/sr0. But to be sure what
    # its device path is, we can query the block devices for a device with the
    # label 'config-2'
    path = server.output_of(
        "lsblk --paths --output LABEL,NAME | grep config-2 | awk '{print $2}'")

    # We can mount this drive as a CD-ROM
    server.assert_run(f'sudo mkdir /mnt/config-drive')
    server.assert_run(f'sudo mount -t iso9660 {path} -o ro /mnt/config-drive')

    # Amongst other things we'll find the UUID of the server in the metadata
    assert server.uuid in server.output_of(
        'cat /mnt/config-drive/openstack/latest/meta_data.json')

    # We can find the same information on the metadata service
    assert server.uuid in server.http_get(
        'http://169.254.169.254/openstack/latest/meta_data.json')


def test_cloud_init_password_on_all_images(create_server):
    """ Using cloud-init, we can set the password of the default user. """

    # Generate a password unique to this test
    password = secrets.token_hex(16)

    # When using a password, the cloud-init config defaults to expire the
    # password upon first login. We override that here, to make testing
    # easier.
    user_data = textwrap.dedent("""
        #cloud-config
        chpasswd:
          expire: false
    """)

    # Create a server with the given password, if the image supports it
    with skip_test_when("This image does not support password logins."):
        server = create_server(
            flavor='flex-4-1',
            password=password,
            user_data=user_data,
        )

    # To verify that the password was set, try and change it to a new
    # password, using the existing one
    new_password = secrets.token_hex(16)

    server.assert_run(oneliner(f"""
        echo -e "{password}\\n{new_password}\\n{new_password}"
        | passwd
    """))
