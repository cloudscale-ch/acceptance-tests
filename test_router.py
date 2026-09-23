"""

Routers
=======

You can connect private networks to each other using routers, or connect
private networks to the internet through routers acting as internet gateways:

"""
import pytest
from dns.resolver import NoNameservers

from constants import PUBLIC_PING_TARGETS
from util import in_parallel, reverse_ptr, nameservers, retry_for


def assert_reverse_pointer(addresses, expect_failure=False):
    """
    Assert that the reverse pointer was published for the given addresses.

    :param addresses: The address dicts to lookup
    :param expect_failure: When set to `True` there should be no PTR record
                           for this address (e.g., after router deletion).
    :return:
    """

    def _assert_reverse_pointer():
        for nameserver in nameservers("cloudscale.ch"):
            for address in addresses:
                if expect_failure:
                    with pytest.raises(NoNameservers):
                        reverse_ptr(address["address"], nameserver)
                else:
                    assert reverse_ptr(address["address"], nameserver) == \
                           address["reverse_ptr"]

    timeout = 60
    address_list = ", ".join([address["address"] for address in addresses])
    message = f"""
    Unexpected reverse PTR for IPs {address_list} after {timeout} seconds.
    """
    retry_for(seconds=timeout).or_fail(
        _assert_reverse_pointer,
        msg=message
    )


def test_internet_gateway(
        internet_gateway,
        create_server,
        create_jumphost,
        private_network,
        image,
):
    """ Test to create an internet gateway. """

    # Using a private network
    subnet = private_network.add_subnet(
        cidr="192.168.100.0/24",
        gateway_address="192.168.100.1",
    )

    # Attach the internet_gateway to the private network
    internet_gateway.add_interface(
        network=private_network.uuid,
        subnet=subnet.uuid,
        address="192.168.100.1"
    )

    # Create jumpost
    jumpost = create_jumphost(private_networks=[private_network])

    # Create a server connected only to its own private network
    private_server = create_server(
        name="private_server",
        image=image,
        interfaces=[{"network": private_network.uuid}],
        jump_host=jumpost,
    )

    # Ping a public IP: Verifies that the server cat access the internet
    private_server.ping(PUBLIC_PING_TARGETS[4], tries=5, wait=1)

    # Verify initial reverse pointers were published
    initial_public_addresses = internet_gateway.internet_gateway_addresses
    assert_reverse_pointer(initial_public_addresses, initial_public_addresses)

    # Disable the internet gateway
    internet_gateway.update(internet_gateway=False)

    # Ping a public IP: Verifies that the server cannot access the internet
    private_server.ping(
        PUBLIC_PING_TARGETS[4],
        tries=5,
        wait=1,
        expect_failure=True
    )

    # Verify initial reverse pointers were reset
    assert_reverse_pointer(initial_public_addresses)

    # Re-enable the internet gateway
    internet_gateway.update(internet_gateway=True)

    # Ping a public IP: Verifies that the server cat access the internet again
    private_server.ping(PUBLIC_PING_TARGETS[4], tries=5, wait=1)

    # Verify updated reverse pointers were published
    updated_public_addresses = internet_gateway.internet_gateway_addresses
    assert_reverse_pointer(updated_public_addresses)


def test_router_connected_private_networks(
        router,
        create_server,
        create_jumphost,
        create_private_network,
        image,
):
    """ Test to create a router between two private networks. """

    # Create two private networks
    private_network_a = create_private_network()
    private_network_b = create_private_network()
    subnet_a = private_network_a.add_subnet(
        cidr="192.168.10.0/24",
        gateway_address="192.168.10.1",
    )
    subnet_b = private_network_b.add_subnet(
        cidr="192.168.11.0/24",
        gateway_address="192.168.11.1",
    )

    # Attach the router to the private networks
    router.add_interface(
        network=private_network_a.uuid,
        subnet=subnet_a.uuid,
        address="192.168.10.1"
    )
    router.add_interface(
        network=private_network_b.uuid,
        subnet=subnet_b.uuid,
        address="192.168.11.1"
    )

    # Create jumpost
    jumphost = create_jumphost(private_networks=[
        private_network_a,
        private_network_b
    ])

    # Create servers each connected only to it"s own private network
    s1, s2 = in_parallel(create_server, instances=(
        {
            "name": "s1",
            "image": image,
            "interfaces": [{"network": private_network_a.uuid}],
            "jump_host": jumphost,
        },
        {
            "name": "s2",
            "image": image,
            "interfaces": [{"network": private_network_b.uuid}],
            "jump_host": jumphost,
        },
    ))

    # Each server can ping the other over private IPv4
    s1.ping(s2.ip("private", 4), tries=5, wait=1)
    s2.ping(s1.ip("private", 4), tries=5, wait=1)
