"""

Infrastructure Functionality
============================

"""

import pytest
import time


@pytest.mark.parametrize('ip_version', [4, 6])
def test_outgoing_smtp_block_ip(server, ip_version):
    """ All outbound SMTP (Port 25) traffic is blocked by default.

    Port 25 outbound is blocked for all IPv4 and IPv6 addresses,
    but can be enabled for upon request.

    """

    # Get the source IP addresses
    ip = server.ip('public', ip_version)

    server.run('sudo apt update')
    server.run('sudo apt install netcat-openbsd -y')

    # Allow up to 1 minute until we expect connections to be blocked
    until = time.monotonic() + 60

    while time.monotonic() < until:
        connect_result = server.run(
            f'nc -vz -{ip_version} -w 5 -s {ip} mail.cloudscale.ch 25'
        )

        if connect_result.rc == 1:
            break

        time.sleep(1)

    assert 'Connection refused' in connect_result.stderr \
        or 'timed out' in connect_result.stderr


@pytest.mark.parametrize(
    "floating_type, ip_version",
    [
        ("floating_ipv4", "4"),
        ("floating_ipv6", "6"),
        ("floating_network", "6"),
    ],
)
def test_outgoing_smtp_block_floating_ip(
    server,
    floating_type,
    ip_version,
    floating_ipv4,
    floating_ipv6,
    floating_network
):
    """ All outbound SMTP (Port 25) traffic is blocked by default.

    Port 25 outbound is blocked for all Floating IPv4 and IPv6
    addresses and networks, but can be enabled for certain customers
    upon request.

    """

    # Get the IP
    floating_map = {
        "floating_ipv4": floating_ipv4,
        "floating_ipv6": floating_ipv6,
        "floating_network": floating_network.network[1],
    }

    ip = floating_map[floating_type]

    # Assign and configure the Floating IP to the server
    if floating_type == "floating_network":
        floating_network.assign(server)
    else:
        ip.assign(server)

    server.configure_floating_ip(ip)

    server.run('sudo apt update')
    server.run('sudo apt install netcat-openbsd -y')

    # Allow up to 1 minute until we expect connections to be blocked
    until = time.monotonic() + 60

    while time.monotonic() < until:
        connect_result = server.run(
            f'nc -vz -{ip_version} -w 5 -s {ip} mail.cloudscale.ch 25'
        )

        if connect_result.rc == 1:
            break

        time.sleep(1)

    assert 'Connection refused' in connect_result.stderr \
        or 'timed out' in connect_result.stderr
