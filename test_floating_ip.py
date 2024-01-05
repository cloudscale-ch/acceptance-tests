"""

Floating IP Acceptance Tests
============================

Using Floating IP, customers can implement high availability on the software
level using the cloudscale.ch cloud.

"""

from util import assert_takes_no_longer_than
from util import in_parallel


def test_floating_ip_connectivity(prober, server, floating_ip):
    """ Floating IPs can be assigned to servers after they have been started.

    """

    # Assign the Floating IP to the server
    floating_ip.assign(server)

    # Configure the Floating IP on the server
    server.configure_floating_ip(floating_ip)

    # Check if the Floating IP is reachable (wait up to 10 seconds)
    prober.ping(floating_ip, count=1, tries=10)

    # Expect 30 successful pings within 15 seconds
    prober.ping(floating_ip, count=30, interval=0.5)


def test_multiple_floating_ips(prober, server, create_floating_ip):
    """ A server may have up to fifteen Floating IPs assigned to it. """

    # Create and assign Floating IPs
    ips = [
        create_floating_ip(
            ip_version=4,
            server=server.uuid
        ) for _ in range(15)
    ]

    # Configure the Floating IPs on the server
    for ip in ips:
        server.configure_floating_ip(ip)

    # Make sure each Floating IP can be pinged from the prober
    for ip in ips:

        # Wait up to 15 seconds for the change to propagate
        prober.ping(ip, timeout=1, tries=15)


def test_floating_ip_stability(prober, create_server, server_group,
                               floating_ipv4, floating_ipv6):
    """ Floating IPs can be moved between servers for high availability.

    Here we test a manual Floating IP move where the configuration of both
    servers is changed as the Floating IP is moved. We ensure that after this
    has happened, no packets are lost.

    """

    # Create two server to assign the Floating IPs
    s1, s2 = in_parallel(create_server, instances=(
        {'name': 's1', 'server_groups': [server_group.uuid]},
        {'name': 's2', 'server_groups': [server_group.uuid]},
    ))

    for floating_ip in floating_ipv4, floating_ipv6:

        # Configure the Floating IP on each server
        for s in s1, s2:

            # Assign the address to one server
            floating_ip.assign(s)

            # Configure the address on that server
            s.configure_floating_ip(floating_ip)

            # Wait up to 15 seconds for the change to propagate
            prober.ping(floating_ip, timeout=1, tries=15)

            # Expect 60 successful pings in 30 seconds
            prober.ping(floating_ip, interval=0.5, count=60)

            # Make sure the DHCP assigned IP still works
            prober.ping(s.ip('public', floating_ip.version))

            # Drop the interface to ensure the next test hits the right target
            s.unconfigure_floating_ip(floating_ip)

            # Check that the Floating IP is no longer reachable
            prober.ping(floating_ip, expect_failure=True)


def test_floating_ip_failover(prober, create_server, server_group,
                              floating_ipv4, floating_ipv6):
    """ Floating IPs can be moved between servers for high availability.

    Here we test a manual Floating IP move, where only the API is changed and
    the servers are not informed of the change.

    """

    # Create two server to assign the Floating IPs
    s1, s2 = in_parallel(create_server, instances=(
        {'name': 's1', 'server_groups': [server_group.uuid]},
        {'name': 's2', 'server_groups': [server_group.uuid]},
    ))

    # Install nginx on the two servers to get unique content for each server
    for s in s1, s2:
        s.assert_run('sudo apt update --allow-releaseinfo-change')
        s.assert_run('sudo apt install -y nginx')

    # Set unique content for each server
    s1.assert_run('echo s1 | sudo dd of=/var/www/html/index.html')
    s2.assert_run('echo s2 | sudo dd of=/var/www/html/index.html')

    for floating_ip in floating_ipv4, floating_ipv6:

        # Point the Floating IP to the first server
        floating_ip.assign(s1)

        # Configure the Floating IP on both servers
        for s in s1, s2:
            s.configure_floating_ip(floating_ip)

        # Try to get the content within ten seconds
        prober.wait_for_http_content(floating_ip, 's1', seconds=10)

        # Point the Floating IP to the second server
        floating_ip.assign(s2)

        # Try to get the content within ten seconds
        prober.wait_for_http_content(floating_ip, 's2', seconds=10)


def test_floating_ip_mass_failover(prober, create_server, server_group,
                                   create_floating_ip):
    """ Floating IP addresses can be re-assigned en masse. """

    # Create two server to assign the Floating IPs
    s1, s2 = in_parallel(create_server, instances=(
        {'name': 's1', 'server_groups': [server_group.uuid]},
        {'name': 's2', 'server_groups': [server_group.uuid]},
    ))

    # Create, assign, and configure 15 Floating IPs
    ips = [
        create_floating_ip(
            ip_version=4,
            server=s1.uuid
        ) for _ in range(15)
    ]

    for ip in ips:
        s1.configure_floating_ip(ip)
        s2.configure_floating_ip(ip)

    assert s1.reachable_via_ip(*ips, timeout=15)

    # Move the Floating IPs to s2
    with assert_takes_no_longer_than(seconds=30):
        for ip in ips:
            ip.assign(s2)

        assert s2.reachable_via_ip(*ips, timeout=15)

    # Move the Floating IPs to s1
    with assert_takes_no_longer_than(seconds=30):
        for ip in ips:
            ip.assign(s1)

        assert s1.reachable_via_ip(*ips, timeout=15)


def test_floating_network(prober, server, floating_network):
    """ Floating Networks (IPv6) can be used to assign a large number
    of addresses to a single server to act as a firewall for the other
    instances.

    """

    # Assign the floating network to the server
    floating_network.assign(server)

    # The Floating Network has a prefix length of 56
    assert floating_network.network.prefixlen == 56

    # We can now ping any address in the network
    test_addresses = (
        floating_network.network[0],
        floating_network.network[1000],
        floating_network.network[-1],
    )

    for address in test_addresses:

        # Configure the address
        server.configure_floating_ip(address)

        # Wait up to 10 seconds for the change to propagate
        prober.ping(address, timeout=1, tries=10)

        # Expect twenty successful pings in 10 seconds
        prober.ping(address, interval=0.5, count=20)

        # Take it down
        server.unconfigure_floating_ip(address)

        # Make sure it is gone
        prober.ping(address, expect_failure=True)
