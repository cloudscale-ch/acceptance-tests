"""

Nested Virtualization Acceptance Tests
======================================

Customers can start virtual servers inside VMs (nested virtualiziation).

"""

from util import oneliner


def test_virtualization_support(server):
    """ Nested virtualization is supported. """

    # List of virt-host-validate checks that do NOT need to "PASS"
    virt_validate_pass_exceptions = {
        "Checking for device assignment IOMMU support",
        "Checking for secure guest support",
        "Checking for cgroup 'freezer' controller support",
    }

    # Install the required package
    server.run('sudo apt update')
    server.run('sudo apt install -y libvirt-clients')

    virt_validate_status = server.run('sudo virt-host-validate').stdout

    # Validate all checks PASS except for the ones defined above
    for line in virt_validate_status.splitlines():
        parts = line.split(':')
        description = parts[1].strip()
        status = parts[2].strip().split()[0]

        if description not in virt_validate_pass_exceptions:
            assert status == "PASS"


def test_run_nested_vm(server):
    """ Nested virtualization is supported. """

    vm_os = 'alpine'   # Needs to match one virt os-variant
    vm_iso_url = 'https://at-images.objects.lpg.cloudscale.ch/alpine.qcow2'

    # Install the required package
    server.run('sudo apt update')
    server.run(oneliner(f"""
        sudo apt install -y
            libvirt-clients
            qemu-kvm
            libvirt-daemon-system
            bridge-utils
            virt-manager
    """))

    # Make sure qemu tests pass and rc == 0
    assert server.output_of('sudo virt-host-validate --help')

    server.run(f'wget {vm_iso_url}')
    server.run('sudo virsh net-start default')
    server.run('sudo virsh net-autostart default')

    server.run(oneliner(f"""
        sudo virt-install
            --virt-type kvm
            --name {vm_os}_vm
            --ram 1024
            --vcpus=1
            --disk path={vm_os}.qcow2,format=qcow2,bus=virtio
            --autoconsole none
            --os-variant generic
            --hvm
            --import
            --serial file,path=/var/log/{vm_os}_vm.log
    """))

    # Return the part of the table that has the actual state for the VM
    vm_status = server.output_of(oneliner("""
        sudo virsh -c qemu:///system list
        | sed -n 3p
    """))

    assert vm_os in vm_status and 'running' in vm_status

    # Wait until the VM is actually booted and we see the welcome message
    # in the console log. Use a timeout of 40s so the VM has time to actually
    # boot up.
    server.wait_for_text_in_file(
        f'/var/log/{vm_os}_vm.log',
        'Welcome to Alpine Linux',
        40
    )
