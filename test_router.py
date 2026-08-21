"""

Routers
=======

You can connect private networks to each other using routers, or connect
private networks to the internet through routers acting as internet gateways:

"""

from util import in_parallel


def test_internet_gateway(
        internet_gateway,
        create_server,
        create_jumphost,
        private_network,
        image,
):
    """ Test to create an internet gateway. """

    # Using a private network
    subnet = private_network.add_subnet(cidr='192.168.100.0/24',
                                        gateway_address='192.168.100.1',
                                        )

    # Attach the internet_gateway to the private network
    internet_gateway.add_interface(
        network=private_network.uuid,
        subnet=subnet.uuid,
        address="192.168.100.1"
    )

    # Create prober/jumpost
    prober = create_jumphost(private_networks=[private_network])

    # Create a server connected only to it's own private network
    private_server = create_server(
        name='private_server',
        image=image,
        interfaces=[{'network': private_network.uuid}],
        jump_host=prober,
    )

    assert internet_gateway.status == 'active'
    assert internet_gateway.internet_gateway
    prober.ping(private_server.ip('private', 4))

    # Ping a public IP
    private_server.ping('8.8.8.8', tries=5, wait=1)


def test_router_connected_private_networks(
        router,
        create_server,
        create_jumphost,
        create_private_network,
        image,
):
    """ Test to create an router between two private networks. """

    # Create two private networks
    private_network_a = create_private_network()
    private_network_b = create_private_network()
    subnet_a = private_network_a.add_subnet(cidr='192.168.10.0/24',
                                            gateway_address='192.168.10.1',
                                            )
    subnet_b = private_network_b.add_subnet(cidr='192.168.11.0/24',
                                            gateway_address='192.168.11.1',
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

    # Create prober/jumpost
    jumphost = create_jumphost(private_networks=[private_network_a,
                                                 private_network_b])

    # Create servers each connected only to it's own private network
    s1, s2 = in_parallel(create_server, instances=(
        {
            'name': 's1',
            'image': image,
            'interfaces': [{'network': private_network_a.uuid}],
            'jump_host': jumphost,
        },
        {
            'name': 's2',
            'image': image,
            'interfaces': [{'network': private_network_b.uuid}],
            'jump_host': jumphost,
        },
    ))

    assert router.status == 'active'
    assert not router.internet_gateway

    jumphost.ping(s1.ip('private', 4))
    jumphost.ping(s2.ip('private', 4))

    # Each server can ping the other over private IPv4
    s1.ping(s2.ip('private', 4), tries=5, wait=1)
    s2.ping(s1.ip('private', 4), tries=5, wait=1)
