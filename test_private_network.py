"""

Private Networks
================

You can interconnect your servers securely over private networks:

"""

from util import in_parallel
from util import retry_for


def test_private_ip_address_on_all_images(create_server, image):
    """ Make sure a private IP is assigned to the server """

    # Check this against all common images
    server = create_server(image=image, use_private_network=True)

    # Get the private interface
    interface = server.private_interface

    # If it takes longer than 5 seconds, print a warning
    def assert_a_private_address():
        assert interface.addresses

    retry_for(seconds=5).or_warn(assert_a_private_address, msg=(
        f'{server.name}: No private IP address after 5s'))

    # if this all together takes more than 30 seconds, we count it as a failure
    retry_for(seconds=25).or_fail(assert_a_private_address, msg=(
        f'{server.name}: No private IP address after 30s'))


def test_private_network_connectivity_on_all_images(create_server, image,
                                                    server_group,
                                                    private_network):
    """ Servers can ping each other via their public/private interfaces. """

    # Use the public and a private network
    interfaces = (
        {'network': 'public'},
        {'network': private_network.uuid},
    )

    # Add an IPv4 subnet to the private network
    private_network.add_subnet(cidr='192.168.100.0/24')

    # Create two servers attached to those networks
    s1, s2 = in_parallel(create_server, instances=(
        {
            'name': 's1',
            'image': image,
            'interfaces': interfaces,
            'server_groups': [server_group.uuid],
        },
        {
            'name': 's2',
            'image': image,
            'interfaces': interfaces,
            'server_groups': [server_group.uuid],
        },
    ))

    # Each VM can ping the other over public IPv4
    s1.ping(s2.ip('public', 4), tries=25, wait=1)
    s2.ping(s1.ip('public', 4), tries=25, wait=1)

    # Each VM can ping the other over private IPv4
    s1.ping(s2.ip('private', 4), tries=5, wait=1)
    s2.ping(s1.ip('private', 4), tries=5, wait=1)

    # Each VM can ping the other over public IPv6
    s1.ping(s2.ip('public', 6), tries=25, wait=1)
    s2.ping(s1.ip('public', 6), tries=25, wait=1)

    # Assign a private IPv6 address to each host
    interface = s1.private_interface.name

    s1.assert_run(f'sudo ip -6 address add fd00::1/64 dev {interface}')
    s2.assert_run(f'sudo ip -6 address add fd00::2/64 dev {interface}')

    # Each VM can ping the other over private IPv6
    s1.ping('fd00::2', tries=5, wait=1)
    s2.ping('fd00::1', tries=5, wait=1)


def test_multiple_private_network_interfaces(create_server, image,
                                             server_group,
                                             create_private_network):
    """ Servers can ping each other with up to fifteen different private
    network interfaces.

    """

    # Create fifteen private networks
    list_of_private_nets = in_parallel(create_private_network, instances=(
        {'name': f'net-{i}'} for i in range(15)
    ))

    # Generate subnets on each network
    subnets = [
        net.add_subnet(cidr=f'192.168.{i}.0/24')
        for i, net in enumerate(list_of_private_nets)
    ]

    # Generate interface configurations
    def get_interface_config(server_index):
        public_network_configs = [{'network': 'public'}]
        private_network_configs = [
            {
                "addresses": [
                    {
                        "subnet": subnet.uuid,
                        "address": subnet.cidr.replace('0/24', server_index)
                    }
                ]
            }
            for subnet in subnets
        ]

        return public_network_configs + private_network_configs

    # Create two servers attached to those networks
    s1, s2 = in_parallel(create_server, instances=(
        {
            'name': 's1',
            'image': image,
            'interfaces': get_interface_config('1'),
            'server_groups': [server_group.uuid],
        },
        {
            'name': 's2',
            'image': image,
            'interfaces': get_interface_config('2'),
            'server_groups': [server_group.uuid],
        },
    ))

    # Server can ping other server over every private IPv4
    for octet in range(15):
        s1.ping(f'192.168.{octet}.2', tries=10, wait=1)


def test_no_private_network_port_security(create_server, image, server_group):
    """ Private networks can do what public networks can't:

    * Change the mac address of an interface.
    * Change the IP address of an interface.

    """

    # Get two servers in a private network
    s1, s2 = in_parallel(create_server, instances=(
        {
            'name': 's1',
            'image': image,
            'use_private_network': True,
            'server_groups': [server_group.uuid],
        },
        {
            'name': 's2',
            'image': image,
            'use_private_network': True,
            'server_groups': [server_group.uuid],
        },
    ))

    # Change the MAC addresses of the private interfaces
    private = s1.private_interface.name

    with s1.host.sudo():
        s1.assert_run(f'ip link set dev {private} down')
        s1.assert_run(f'ip link set dev {private} address 02:00:00:00:00:01')
        s1.assert_run(f'ip link set dev {private} up')

    with s2.host.sudo():
        s2.assert_run(f'ip link set dev {private} down')
        s2.assert_run(f'ip link set dev {private} address 02:00:00:00:00:02')
        s2.assert_run(f'ip link set dev {private} up')

    # Ping should still work
    s1.ping(s2.ip('private', 4))
    s2.ping(s1.ip('private', 4))

    # Switch the private IP address of both servers
    s1_address = s1.ip('private', 4)
    s2_address = s2.ip('private', 4)

    s1.assert_run(f'sudo ip addr del {s1_address}/24 dev {private}')
    s2.assert_run(f'sudo ip addr del {s2_address}/24 dev {private}')

    s1.assert_run(f'sudo ip addr add {s2_address}/24 dev {private}')
    s2.assert_run(f'sudo ip addr add {s1_address}/24 dev {private}')

    # Ping should continue to work
    s1.ping(s2.ip('private', 4))
    s2.ping(s1.ip('private', 4))


def test_private_network_without_dhcp(create_server, image, private_network):
    """ We can launch servers in private networks without handing out
    an address via DHCP.

    """

    # Define a custom subnet
    subnet = private_network.add_subnet(cidr='192.168.100.0/24')

    # Initialize each server with an interface attached to the private network.
    # The servers are not given an address by DHCP
    interfaces = [
        {'network': 'public'},
        {'network': private_network.uuid, 'addresses': []}
    ]

    s1, s2 = in_parallel(create_server, instances=(
        {'name': 's1', 'image': image, 'interfaces': interfaces},
        {'name': 's2', 'image': image, 'interfaces': interfaces},
    ))

    # Make sure the address is actually unset
    assert s1.ip('private', 4, fail_if_missing=False) is None
    assert s2.ip('private', 4, fail_if_missing=False) is None

    # Make sure the host did not get an address matching the subnet
    for address in s1.configured_ip_addresses():
        assert address not in subnet

    for address in s2.configured_ip_addresses():
        assert address not in subnet

    # Configure the IP addresses on the servers
    private = s1.private_interface.name

    s1.assert_run(f'sudo ip addr add 192.168.100.1/24 dev {private}')
    s2.assert_run(f'sudo ip addr add 192.168.100.2/24 dev {private}')

    # Now the servers should see each other
    s1.ping('192.168.100.2')
    s2.ping('192.168.100.1')


def test_private_network_mtu(create_server, image, private_network):
    """ Private networks may have their own MTU. """

    # Define a custom subnet
    private_network.add_subnet(cidr='192.168.100.0/24')

    # Manually configure the IP addresses
    interfaces = [
        {'network': 'public'},
        {'network': private_network.uuid, 'addresses': []}
    ]

    s1, s2 = in_parallel(create_server, instances=(
        {'name': 's1', 'image': image, 'interfaces': interfaces},
        {'name': 's2', 'image': image, 'interfaces': interfaces},
    ))

    # Configure the IP addresses on the servers
    private = s1.private_interface.name

    s1.assert_run(f'sudo ip addr add 192.168.100.1/24 dev {private}')
    s2.assert_run(f'sudo ip addr add 192.168.100.2/24 dev {private}')

    # The default MTU for private networks is 9000
    s1.assert_run(f'sudo ip link set dev {private} mtu 9000')
    s2.assert_run(f'sudo ip link set dev {private} mtu 9000')

    # Assert that MTU is at least 1500
    s1.ping('192.168.100.2', size=1472, fragment=False)
    s2.ping('192.168.100.1', size=1472, fragment=False)

    # # Assert that we can use an MTU of 9000
    s1.ping('192.168.100.2', size=8972, fragment=False)
    s2.ping('192.168.100.1', size=8972, fragment=False)

    # Assert that an MTU higher than 9000 fails
    s1.ping('192.168.100.2', size=8973, fragment=False, expect_failure=True)
    s2.ping('192.168.100.1', size=8973, fragment=False, expect_failure=True)

    # Change the MTU to 4500
    private_network.change_mtu(4500)

    s1.assert_run(f'sudo ip link set dev {private} mtu 4500')
    s2.assert_run(f'sudo ip link set dev {private} mtu 4500')

    # Assert that MTU is at least 1500
    s1.ping('192.168.100.2', size=1472, fragment=False)
    s2.ping('192.168.100.1', size=1472, fragment=False)

    # # Assert that we can use an MTU of 4500
    s1.ping('192.168.100.2', size=4472, fragment=False)
    s2.ping('192.168.100.1', size=4472, fragment=False)

    # Assert that an MTU higher than 4500 fails
    s1.ping('192.168.100.2', size=4473, fragment=False, expect_failure=True)
    s2.ping('192.168.100.1', size=4473, fragment=False, expect_failure=True)


def test_private_network_only_on_all_images(prober, create_server, image):
    """ Servers can be created without any public interface, typcially to hide
    them behind a firewall. Those servers can only be reached through the
    private network they are attached to.

    """

    # Create a server without a public interface, reachable via a jump-host
    server = create_server(
        image=image,
        use_public_network=False,
        use_private_network=True,
        jump_host=prober)

    # Make sure we can reach the private network from the jump-host
    prober.ping(server.ip('private', 4))

    # Make sure that no public interface is configured
    public_addresses = server.configured_ip_addresses(is_public=True)
    assert len(public_addresses) == 0


def test_private_network_attach_later(server, private_network):
    """ Private network ports can be attached to an already running server.

    """

    subnet = private_network.add_subnet('10.0.0.0/24')

    # Attach the server to the private network
    server.update(
        interfaces=[{"network": "public"},
                    {"network": private_network.info["uuid"]}]
    )

    # Assert the private network interface now exists
    assert server.private_interface.exists

    def assert_private_network_is_configured():
        # Check if the private interface is configured with exactly one
        # address from the subnet
        private_addresses = server.configured_ip_addresses(
            is_private=True,
            is_loopback=False,
            is_link_local=False,
            version=4,
        )
        assert len(private_addresses) == 1
        assert private_addresses[0] in subnet

    retry_for(seconds=30).or_fail(
        assert_private_network_is_configured,
        msg='Failed to configure private network.',
    )
