# Acceptance Tests for the cloudscale.ch IaaS Platform

To ensure that our cloud platform continues to meet our quality standards over time, we use a set of acceptance tests to validate various aspects of our offering:

* Features work as documented.
* Response times meet our expectations.
* Regressions are avoided.

These tests are run regularly against our public infrastructure as well as our internal test environment where upgrades are staged prior to rollout.

<a href="https://github.com/cloudscale-ch/acceptance-tests/actions/workflows/acceptance-tests-in-lpg1.yml"><img src="https://github.com/cloudscale-ch/acceptance-tests/actions/workflows/acceptance-tests-in-lpg1.yml/badge.svg" title="Result of last acceptance test run in LPG1"></a> <a href="https://github.com/cloudscale-ch/acceptance-tests/actions/workflows/acceptance-tests-in-rma1.yml"><img src="https://github.com/cloudscale-ch/acceptance-tests/actions/workflows/acceptance-tests-in-rma1.yml/badge.svg" title="Result of last acceptance test run in RMA1"></a>

## Implemented Tests

| Category            | Test Name                                                                        | Images   |
|---------------------|----------------------------------------------------------------------------------|----------|
| **API**             | [test_duplicate_headers](./test_api.py#L15)                                      | -        |
|                     | [test_invalid_duplicate_headers](./test_api.py#L31)                              | -        |
|                     | [test_cors_headers](./test_api.py#L53)                                           | -        |
| **Custom Image**    | [test_custom_image_with_slug](./test_custom_image.py#L11)                        | custom   |
|                     | [test_custom_image_with_uuid](./test_custom_image.py#L22)                        | custom   |
|                     | [test_custom_image_with_uefi](./test_custom_image.py#L33)                        | custom   |
| **Floating IP**     | [test_floating_ip_connectivity](./test_floating_ip.py#L14)                       | default  |
|                     | [test_multiple_floating_ips](./test_floating_ip.py#L32)                          | default  |
|                     | [test_floating_ip_stability](./test_floating_ip.py#L54)                          | default  |
|                     | [test_floating_ip_failover](./test_floating_ip.py#L97)                           | default  |
|                     | [test_floating_network](./test_floating_ip.py#L140)                              | default  |
| **Lbaas**           | [test_simple_tcp_load_balancer](./test_lbaas.py#L24)                             | default  |
|                     | [test_load_balancer_end_to_end](./test_lbaas.py#L48)                             | default  |
|                     | [test_multiple_listeners](./test_lbaas.py#L81)                                   | default  |
|                     | [test_multiple_listeners_multiple_pools](./test_lbaas.py#L113)                   | default  |
|                     | [test_balancing_algorithm_round_robin](./test_lbaas.py#L166)                     | default  |
|                     | [test_balancing_algorithm_source_ip](./test_lbaas.py#L202)                       | default  |
|                     | [test_balancing_algorithm_least_connections](./test_lbaas.py#L251)               | default  |
|                     | [test_backend_health_monitors](./test_lbaas.py#L292)                             | default  |
|                     | [test_pool_member_change](./test_lbaas.py#L372)                                  | default  |
|                     | [test_private_load_balancer_frontend](./test_lbaas.py#L464)                      | default  |
|                     | [test_floating_ip](./test_lbaas.py#L506)                                         | default  |
|                     | [test_floating_ip_reassign](./test_lbaas.py#L540)                                | default  |
|                     | [test_frontend_allowed_cidr](./test_lbaas.py#L621)                               | default  |
|                     | [test_proxy_protocol](./test_lbaas.py#L696)                                      | default  |
| **Private Network** | [test_private_ip_address_on_all_images](./test_private_network.py#L14)           | all      |
|                     | [test_private_network_connectivity_on_all_images](./test_private_network.py#L35) | all      |
|                     | [test_multiple_private_network_interfaces](./test_private_network.py#L88)        | default  |
|                     | [test_no_private_network_port_security](./test_private_network.py#L145)          | default  |
|                     | [test_private_network_without_dhcp](./test_private_network.py#L201)              | default  |
|                     | [test_private_network_mtu](./test_private_network.py#L244)                       | default  |
|                     | [test_private_network_only_on_all_images](./test_private_network.py#L302)        | all      |
|                     | [test_private_network_attach_later](./test_private_network.py#L324)              | default  |
| **Public Network**  | [test_public_ip_address_on_all_images](./test_public_network.py#L22)             | all      |
|                     | [test_public_network_connectivity_on_all_images](./test_public_network.py#L51)   | all      |
|                     | [test_public_network_mtu](./test_public_network.py#L70)                          | default  |
|                     | [test_public_network_port_security](./test_public_network.py#L97)                | default  |
|                     | [test_public_network_ipv4_only_on_all_images](./test_public_network.py#L181)     | all      |
|                     | [test_reverse_ptr_record_of_server](./test_public_network.py#L202)               | default  |
|                     | [test_reverse_ptr_record_of_floating_ip](./test_public_network.py#L226)          | default  |
| **Server**          | [test_change_flavor_from_flex_to_flex](./test_server.py#L18)                     | default  |
|                     | [test_change_flavor_from_flex_to_plus](./test_server.py#L40)                     | default  |
|                     | [test_change_flavor_from_plus_to_flex](./test_server.py#L62)                     | default  |
|                     | [test_change_flavor_from_plus_to_plus](./test_server.py#L84)                     | default  |
|                     | [test_hostname](./test_server.py#L106)                                           | default  |
|                     | [test_rename_server](./test_server.py#L124)                                      | default  |
|                     | [test_reboot_server](./test_server.py#L148)                                      | default  |
|                     | [test_stop_and_start_server](./test_server.py#L176)                              | default  |
|                     | [test_rename_server_group](./test_server.py#L205)                                | default  |
|                     | [test_no_cpu_steal_on_plus_flavor](./test_server.py#L215)                        | default  |
|                     | [test_random_number_generator](./test_server.py#L247)                            | default  |
|                     | [test_metadata_on_all_images](./test_server.py#L262)                             | all      |
| **Volume**          | [test_attach_and_detach_volume_on_all_images](./test_volume.py#L22)              | all      |
|                     | [test_expand_volume_online_on_all_images](./test_volume.py#L57)                  | all      |
|                     | [test_expand_filesystem_online_on_common_images](./test_volume.py#L82)           | common   |
|                     | [test_expand_filesystem_on_boot_on_common_images](./test_volume.py#L124)         | common   |
|                     | [test_maximum_number_of_volumes](./test_volume.py#L152)                          | default  |

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
invoke follow
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
