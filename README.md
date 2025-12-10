# Acceptance Tests for the cloudscale.ch IaaS Platform

To ensure that our cloud platform continues to meet our quality standards over time, we use a set of acceptance tests to validate various aspects of our offering:

* Features work as documented.
* Response times meet our expectations.
* Regressions are avoided.

These tests are run regularly against our public infrastructure as well as our internal test environment where upgrades are staged prior to rollout.

<a href="https://github.com/cloudscale-ch/acceptance-tests/actions/workflows/acceptance-tests-in-lpg1.yml"><img src="https://github.com/cloudscale-ch/acceptance-tests/actions/workflows/acceptance-tests-in-lpg1.yml/badge.svg" title="Result of last acceptance test run in LPG1"></a> <a href="https://github.com/cloudscale-ch/acceptance-tests/actions/workflows/acceptance-tests-in-rma1.yml"><img src="https://github.com/cloudscale-ch/acceptance-tests/actions/workflows/acceptance-tests-in-rma1.yml/badge.svg" title="Result of last acceptance test run in RMA1"></a>

## Implemented Tests

| Category                  | Test Name                                                                        | Images   |
|---------------------------|----------------------------------------------------------------------------------|----------|
| **API**                   | [test_duplicate_headers](./test_api.py#L17)                                      | -        |
|                           | [test_invalid_duplicate_headers](./test_api.py#L33)                              | -        |
|                           | [test_cors_headers](./test_api.py#L55)                                           | -        |
|                           | [test_project_log](./test_api.py#L80)                                            | -        |
| **Custom Image**          | [test_custom_image_with_slug](./test_custom_image.py#L11)                        | custom   |
|                           | [test_custom_image_with_uuid](./test_custom_image.py#L22)                        | custom   |
|                           | [test_custom_image_with_uefi](./test_custom_image.py#L33)                        | custom   |
| **Floating IP**           | [test_floating_ip_connectivity](./test_floating_ip.py#L15)                       | default  |
|                           | [test_multiple_floating_ips](./test_floating_ip.py#L33)                          | default  |
|                           | [test_floating_ip_stability](./test_floating_ip.py#L55)                          | default  |
|                           | [test_floating_ip_failover](./test_floating_ip.py#L98)                           | default  |
|                           | [test_floating_ip_mass_failover](./test_floating_ip.py#L141)                     | default  |
|                           | [test_floating_network](./test_floating_ip.py#L180)                              | default  |
| **Load Balancer**         | [test_simple_tcp_load_balancer](./test_load_balancer.py#L25)                     | default  |
|                           | [test_simple_udp_load_balancer](./test_load_balancer.py#L49)                     | default  |
|                           | [test_load_balancer_end_to_end](./test_load_balancer.py#L79)                     | default  |
|                           | [test_multiple_listeners](./test_load_balancer.py#L153)                          | default  |
|                           | [test_multiple_listeners_multiple_pools](./test_load_balancer.py#L185)           | default  |
|                           | [test_balancing_algorithm_round_robin](./test_load_balancer.py#L238)             | default  |
|                           | [test_balancing_algorithm_source_ip](./test_load_balancer.py#L274)               | default  |
|                           | [test_balancing_algorithm_least_connections](./test_load_balancer.py#L323)       | default  |
|                           | [test_backend_health_monitors](./test_load_balancer.py#L364)                     | default  |
|                           | [test_pool_member_change](./test_load_balancer.py#L444)                          | default  |
|                           | [test_private_load_balancer_frontend](./test_load_balancer.py#L536)              | default  |
|                           | [test_floating_ip](./test_load_balancer.py#L580)                                 | default  |
|                           | [test_floating_ip_reassign](./test_load_balancer.py#L614)                        | default  |
|                           | [test_frontend_allowed_cidr](./test_load_balancer.py#L737)                       | default  |
|                           | [test_proxy_protocol](./test_load_balancer.py#L812)                              | default  |
|                           | [test_ping](./test_load_balancer.py#L855)                                        | default  |
| **Nested Virtualization** | [test_virtualization_support](./test_nested_virtualization.py#L14)               | default  |
|                           | [test_run_nested_vm](./test_nested_virtualization.py#L40)                        | default  |
| **Private Network**       | [test_private_ip_address_on_all_images](./test_private_network.py#L14)           | all      |
|                           | [test_private_network_connectivity_on_all_images](./test_private_network.py#L35) | all      |
|                           | [test_multiple_private_network_interfaces](./test_private_network.py#L88)        | default  |
|                           | [test_no_private_network_port_security](./test_private_network.py#L145)          | default  |
|                           | [test_private_network_without_dhcp](./test_private_network.py#L242)              | default  |
|                           | [test_private_network_mtu](./test_private_network.py#L288)                       | default  |
|                           | [test_private_network_only_on_all_images](./test_private_network.py#L349)        | all      |
|                           | [test_private_network_attach_later](./test_private_network.py#L371)              | default  |
|                           | [test_private_network_dhcp_dns_replies](./test_private_network.py#L408)          | default  |
| **Public Network**        | [test_public_ip_address_on_all_images](./test_public_network.py#L22)             | all      |
|                           | [test_public_network_connectivity_on_all_images](./test_public_network.py#L51)   | all      |
|                           | [test_public_network_mtu](./test_public_network.py#L70)                          | default  |
|                           | [test_public_network_port_security](./test_public_network.py#L102)               | default  |
|                           | [test_public_network_ipv4_only_on_all_images](./test_public_network.py#L186)     | all      |
|                           | [test_reverse_ptr_record_of_server](./test_public_network.py#L207)               | default  |
|                           | [test_reverse_ptr_record_of_floating_ip](./test_public_network.py#L231)          | default  |
| **Server**                | [test_change_flavor_from_flex_to_flex](./test_server.py#L21)                     | default  |
|                           | [test_change_flavor_from_flex_to_plus](./test_server.py#L43)                     | default  |
|                           | [test_change_flavor_from_plus_to_flex](./test_server.py#L65)                     | default  |
|                           | [test_change_flavor_from_plus_to_plus](./test_server.py#L87)                     | default  |
|                           | [test_hostname](./test_server.py#L109)                                           | default  |
|                           | [test_rename_server](./test_server.py#L127)                                      | default  |
|                           | [test_reboot_server](./test_server.py#L151)                                      | default  |
|                           | [test_stop_and_start_server](./test_server.py#L179)                              | default  |
|                           | [test_rename_server_group](./test_server.py#L208)                                | default  |
|                           | [test_no_cpu_steal_on_plus_flavor](./test_server.py#L218)                        | default  |
|                           | [test_random_number_generator](./test_server.py#L250)                            | default  |
|                           | [test_metadata_on_all_images](./test_server.py#L265)                             | all      |
|                           | [test_cloud_init_password_on_all_images](./test_server.py#L292)                  | all      |
| **Volume**                | [test_attach_and_detach_volume_on_all_images](./test_volume.py#L23)              | all      |
|                           | [test_expand_volume_online_on_all_images](./test_volume.py#L58)                  | all      |
|                           | [test_expand_filesystem_online_on_common_images](./test_volume.py#L83)           | common   |
|                           | [test_expand_filesystem_on_boot_on_common_images](./test_volume.py#L125)         | common   |
|                           | [test_maximum_number_of_volumes](./test_volume.py#L153)                          | default  |
|                           | [test_snapshot_volume_attached](./test_volume.py#L184)                           | default  |
|                           | [test_snapshot_volume_detached](./test_volume.py#L251)                           | default  |
|                           | [test_snapshot_root_volume](./test_volume.py#L324)                               | default  |
|                           | [test_snapshots_in_multiple_steps](./test_volume.py#L384)                        | default  |
|                           | [test_volume_from_snapshot](./test_volume.py#L480)                               | default  |
|                           | [test_volume_from_root_snapshot](./test_volume.py#L524)                          | default  |

## Warning

> ⚠️ Running these tests yourself may incur unexpected costs and may result in data loss if run against a production account with live systems. Therefore, we strongly advise you to use a separate account for these tests.

## Installation

> ℹ︎ Note that you need at least Python 3.6.

To install the tests, you have to clone the repository:

```console
git clone git@github.com:cloudscale-ch/acceptance-tests.git
```

Now, every time you want to run the tests in a new shell, use the following command first:

```console
source acceptance-tests/pre-flight
```

You will be automatically switched to the acceptance-tests directory, ready to run the tests as outlined below.

## Running Tests

To run all tests, run py.test as follows:

```console
py.test .
```

### Running Individual Tests

To only run a specific test, run py.test as follows:

```console
py.test . -k <test-name>
```

### Running Tests Against a Specific Image

By default, all tests are run against the default image, most tests are run against a set of common images, and some tests are run against all images provided by cloudscale.ch.

To run all tests against a specific image, use this image as the default:

```console
py.test --default-image ubuntu-20.04 --default-image-only
```

Note that our default image is Debian 10. If you pick a different default image your results may differ.

### Running Tests Against a Custom Image

Custom images can be used as the default image by specifying their slug, along with a username that can be used to connect via SSH. Note that custom images are less likely to pass all tests without prior modification, as the acceptance tests mainly focus on our common images.

```console
py.test --default-image custom:alpine --default-image-only --username alpine
```

### Running Tests Against a Specific Zone

By default, tests are run against a randomly selected zone.

Alternatively, you can specify the zone to run the tests against:

```console
py.test --zone rma1
py.test --zone lpg1
```

### Connect to Test Hosts

During test development, it can be useful to manually connect to hosts created by the tests. In this case it is necessary to explicitly specify your own SSH key, since tests connect to hosts using temporary SSH keys only:

```console
py.test --ssh-key ~/.ssh/id_rsa.pub
```

## Running a Test Multiple Times

Sometimes it is useful to run a specific test multiple times in a row:

```console
py.test --count=10 test_floating_ip.py
```

## Events Log

During execution, the acceptance tests generate a detailed log in the `events` directory (one file per test-run). Each line in such an event log is a structured JSON object.

Using a custom command, you can create a human-readable output of this log:

```console
invoke pretty-print --file events/<file>
```

You can include filters as well:

```console
invoke pretty-print --file events/<file> --regex outcome=failed
```

Or, during test execution, you can follow the log in a separate terminal window while it is being written. This will tail all the event logs that are currently being written. No need to specify a single file.

```console
invoke tail
```

## Cleanup

During normal operation, all resources created by the acceptance tests are automatically cleaned up. However, if the process receives a `SIGKILL` signal, or if it crashes, there may be resources left afterwards.

If you want to be sure, you can clean up all resources created by any acceptance test using the cleanup command:

```console
invoke cleanup
```

All resources created by acceptance tests receive a unique tag, based on a securely hashed version of the API token, so using this command should be reasonably safe. However, we still strongly advise you to use a separate account for these tests as a precaution.

## Developing New Tests

### Create a New Branch

In order to review tests and to be able to develop multiple tests in parallel, they should be developed in a separate Git branch:

```console
git branch <your_branch_name>
```

### Writing Tests to be Run Against Specific Images

If you write a test with the `image` fixture, it will be called with the default image. This default image can be changed using the `--default-image` command line parameter.

If you want to ensure that a test runs against all common images, use the `image` fixture and include `all_images` in the name of your test:

```python
def test_all_images_have_a_hosts_file(create_server, image):
    server = create_server(image=image)
```

If you use `common_images` in the name of your test, only common images will be tested:

```python
def test_common_images_have_a_hosts_file(create_server, image):
    server = create_server(image=image)
```

### Update README tests table

If you add new tests, update the tests table:

```console
invoke implemented-tests-table
```

### Commit Your Test

```console
git add <new_or_changed_files>
git commit
```

### Push Your Branch and Create a Pull Request

```console
git push origin <your_branch_name>
```

To create a pull request follow the link that will be displayed upon pushing a branch.
