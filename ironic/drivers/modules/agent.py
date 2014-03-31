# Copyright 2014 Rackspace, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os
import time

from oslo.config import cfg

from ironic.common import exception
from ironic.common import image_service
from ironic.common import neutron
from ironic.common import paths
from ironic.common import states
from ironic.common import tftp
from ironic.common import utils
from ironic.conductor import utils as manager_utils
from ironic.db import api as dbapi
from ironic.drivers import base
from ironic.drivers.modules import agent_client
from ironic.drivers.modules import agent_utils
from ironic.drivers import utils as driver_utils
from ironic.objects import node as node_module
from ironic.openstack.common import excutils
from ironic.openstack.common import log


"""States:

BUILDING: caching

DEPLOYING: applying instance definition (SSH pub keys, etc), rebooting
ACTIVE: ready to be used

DELETING: doing decom
DELETED: decom finished
"""


agent_opts = [
    cfg.IntOpt('heartbeat_timeout',
                default=300,
                help='The length of time in seconds until the driver will '
                     'consider the agent down. The agent will attempt to '
                     'contact Ironic at some set fraction of this time '
                     '(defaulting to 2/3 the max time).'
                     'Defaults to 5 minutes.'),
    cfg.StrOpt('pxe_config_template',
               default=paths.basedir_def(
                   'drivers/modules/agent_config.template'),
               help='Template file for PXE configuration.'),
    cfg.StrOpt('dhcp_provider',
               default='neutron',
               help='The service responsible for providing DHCP and TFTP to '
                    'booting agents. If set to "neutron", it expects a '
                    'TFTP server local to each conductor where TFTP configs '
                    'can be written out. If set to "external", it expects '
                    'a pre-configured DHCP and TFTP service (such as a DHCP '
                    'server which determines what TFTP image which should be '
                    'booted.)'),
    cfg.StrOpt('agent_kernel_path',
               help='The path to the kernel image used to boot the agent. '
                    'This will be put into the DHCP file if dhcp_provider '
                    'is set to "neutron".'),
    cfg.StrOpt('agent_initrd_path',
               help='The path to the kernel image used to boot the agent. '
                    'This will be put into the DHCP file if dhcp_provider '
                    'is set to "neutron".'),
    cfg.StrOpt('agent_kernel_args',
               default='nofb nomodeset vga=normal',
               help='Additional append parameters for agent boot.'),
    cfg.StrOpt('ironic_api_url',
               help='The address that will be sent to the agent which the '
                    'agent will send its initial lookup call to. Defaults '
                    'to my_ip.',
               default='$my_ip'),
    cfg.StrOpt('pxe_bootfile_name',
               default='pxelinux.0',
               help='Neutron bootfile DHCP parameter.')
    ]

CONF = cfg.CONF
CONF.import_opt('my_ip', 'ironic.netconf')
CONF.register_opts(agent_opts, group='agent')

LOG = log.getLogger(__name__)


def _time():
    return time.time()


def _get_client():
    client = agent_client.AgentClient()
    return client


def _get_neutron_client(context):
    return agent_utils.AgentNeutronAPI(context)


def _set_failed_state(task, msg):
    """Set a node's error state and provision state to signal Nova.

    When deploy steps aren't called by explicitly the conductor, but are
    the result of callbacks, we need to set the node's state explicitly.
    This tells Nova to change the instance's status so the user can see
    their deploy/tear down had an issue and makes debugging/deleting Nova
    instances easier.
    """
    node = task.node
    node.provision_state = states.DEPLOYFAIL
    node.target_provision_state = states.NOSTATE
    node.save(task.context)
    try:
        manager_utils.node_power_action(task, states.POWER_OFF)
    except Exception:
        msg = (_('Node %s failed to power off while handling deploy '
                 'failure. This may be a serious condition. Node '
                 'should be removed from Ironic or put in maintenance '
                 'mode until the problem is resolved.') % node.uuid)
        LOG.error(msg)
    finally:
        # NOTE(deva): node_power_action() erases node.last_error
        #             so we need to set it again here.
        node.last_error = msg
        node.save(task.context)


class AgentDeploy(base.DeployInterface):
    """Interface for deploy-related actions."""

    # applies when https://review.openstack.org/#/c/86744 lands
    valid_states = {
        'deploy': [states.POWER_OFF, states.POWER_ON],
        'destroy': [states.POWER_OFF, states.NOSTATE]
    }

    def validate(self, task, node):
        """Validate the driver-specific Node deployment info.

        This method validates whether the 'instance_info' property of the
        supplied node contains the required information for this driver to
        deploy images to the node.

        :param task: a TaskManager instance
        :param node: a single Node to validate.
        :raises: InvalidParameterValue
        """
        if node.instance_info is None:
            raise exception.InvalidParameterValue(_('instance_info cannot be '
                                                    'null.'))
        required_instance_fields = ['image_source', 'configdrive']
        for field in required_instance_fields:
            if field not in node.instance_info:
                raise exception.InvalidParameterValue(_('%s is required in '
                                                        'instance_info') %
                                                      field)
        if 'agent_url' not in node.driver_info:
            raise exception.InvalidParameterValue(_('agent_url is required in '
                                                    'driver_info.'))

    def deploy(self, task):
        """Perform a deployment to a node.

        Perform the necessary work to deploy an image onto the specified node.
        This method will be called after prepare(), which may have already
        performed any preparatory steps, such as pre-caching some data for the
        node.

        :param task: a TaskManager instance.
        :param node: the Node to act upon.
        :returns: status of the deploy. One of ironic.common.states.
        """
        if task.node.power_state == states.POWER_OFF:
            LOG.info(_('Powering on node and waiting for first heartbeat %s.'),
                     task.node.uuid)
            # Boot the box, wait for the first heartbeat, then deploy.
            manager_utils.node_set_boot_device(task, 'pxe')
            manager_utils.node_power_action(task, states.POWER_ON)

        return states.DEPLOYWAIT

    def tear_down(self, task):
        """Reboot the machine and begin decom.

        When the node reboots, it will check in, see that it is supposed
        to be deleted, and start decom.

        Public networks will already be removed by Nova.

        :param task: a TaskManager instance.
        :param node: the Node to act upon.
        :returns: status of the deploy. One of ironic.common.states.
        """
        LOG.info(_('Tearing down node %s, powering off.'), task.node.uuid)
        # Reboot into ramdisk
        # power off, switch to pxe, switch to decom network, power on
        LOG.info(_('Powering off node %s to switch networks for '
                   'decommissioning'), task.node.uuid)
        manager_utils.node_power_action(task, states.POWER_OFF)
        manager_utils.node_set_boot_device(task, 'pxe', persistent=True)

        LOG.debug('Switching to provisioning network for node %s',
                  task.node.uuid)
        # Remove public network, add provisioning network
        neutron_client = _get_neutron_client(task.context)
        neutron_client.deconfigure_instance_networks(task.node)
        neutron_client.add_provisioning_network(task.node)

        LOG.info(_('Powering on node %s to start decommissioning'),
                 task.node.uuid)
        manager_utils.node_power_action(task, states.POWER_ON)

        # By returning this state rather than deleting, we can clear the
        # node from the user's Nova list, but continue decommissioning, which
        # may take a long time (hours with large spinning disks)
        task.node.target_provision_state = states.DECOMMISSIONED
        return states.DECOMMISSIONING

    def prepare(self, task):
        """Prepare the deployment environment for this node.

        :param task: a TaskManager instance.
        :param node: the Node for which to prepare a deployment environment
                     on this Conductor.
        """
        if CONF.agent.dhcp_provider == 'neutron':
            LOG.info(_('Creating PXE config for node %s.'), task.node.uuid)
            # Create the TFTP file.
            pxe_options = self._get_pxe_config()
            tftp.create_pxe_config(
                task, pxe_options,
                pxe_config_template=CONF.agent.pxe_config_template)

    def clean_up(self, task):
        """Clean up the deployment environment for this node.

        If preparation of the deployment environment ahead of time is possible,
        this method should be implemented by the driver. It should erase
        anything cached by the `prepare` method.

        If implemented, this method must be idempotent. It may be called
        multiple times for the same node on the same conductor, and it may be
        called by multiple conductors in parallel. Therefore, it must not
        require an exclusive lock.

        This method is called before `tear_down`.

        :param task: a TaskManager instance.
        :param node: the Node whose deployment environment should be cleaned up
                     on this Conductor.
        """
        # Unnecessary for external DHCP.
        if CONF.agent.dhcp_provider == 'neutron':
            LOG.info(_('Removing PXE config for node %s.'), task.node.uuid)
            utils.unlink_without_raise(tftp.get_pxe_config_file_path(
                task.node.uuid))
            for port in driver_utils.get_node_mac_addresses(task):
                mac_path = tftp.get_pxe_mac_path(port)
                utils.unlink_without_raise(mac_path)

            utils.rmtree_without_raise(
                os.path.join(CONF.tftp.tftp_root, task.node.uuid))

    def take_over(self, task):
        """Take over management of this node from a dead conductor.

        If conductors' hosts maintain a static relationship to nodes, this
        method should be implemented by the driver to allow conductors to
        perform the necessary work during the remapping of nodes to conductors
        when a conductor joins or leaves the cluster.

        For example, the PXE driver has an external dependency:
            Neutron must forward DHCP BOOT requests to a conductor which has
            prepared the tftpboot environment for the given node. When a
            conductor goes offline, another conductor must change this setting
            in Neutron as part of remapping that node's control to itself.
            This is performed within the `takeover` method.

        :param task: a TaskManager instance.
        :param node: the Node which is now being managed by this Conductor.
        """
        # Unnecessary for external DHCP.
        if CONF.agent.dhcp_provider == 'neutron':
            neutron.update_neutron(task, CONF.agent.pxe_bootfile_name)

    def _get_pxe_config(self):
        #TODO(JoshNang) Make customizing per flavor or instance easier.
        return {
            'deployment_aki_path': CONF.agent.agent_kernel_path,
            'deployment_ari_path': CONF.agent.agent_initrd_path,
            'kernel_command_args': CONF.agent.agent_kernel_args,
            'ipa_api_url': CONF.agent.ironic_api_url,
        }


class AgentVendorInterface(base.VendorInterface):
    def __init__(self):
        self.vendor_routes = {
            'heartbeat': self._heartbeat
        }
        self.driver_routes = {
            'lookup': self._lookup,
        }
        self.supported_payload_versions = [None, '1', '2']
        self.dbapi = dbapi.get_instance()
        self._client = _get_client()

    def validate(self, task, **kwargs):
        """Validate the driver-specific Node deployment info.

        No validation necessary.

        :param task: a TaskManager instance
        :param method: the vendor method to be called after validate
        """
        pass

    def driver_vendor_passthru(self, task, method, **kwargs):
        """A node that does not know its UUID should POST to this method.
        Given method, route the command to the appropriate private function.
        """
        if method not in self.driver_routes:
            raise exception.InvalidParameterValue(_('No handler for method %s')
                                                  % method)
        func = self.driver_routes[method]
        return func(task, **kwargs)

    def vendor_passthru(self, task, **kwargs):
        """A node that knows its UUID should heartbeat to this passthru.

        It will get its node object back, with what Ironic thinks its provision
        state is and the target provision state is.
        """
        method = kwargs['method']  # Existence checked in mixin
        if method not in self.vendor_routes:
            raise exception.InvalidParameterValue(_('No handler for method '
                                                    '%s') % method)
        func = self.vendor_routes[method]
        try:
            return func(task, **kwargs)
        except Exception:
            # catch-all in case something bubbles up here
            with excutils.save_and_reraise_exception():
                LOG.exception(_('vendor_passthru failed with method %s'),
                              method)

    def _heartbeat(self, task, **kwargs):
        """Method for agent to periodically check in.

        The agent should be sending its agent_url (so Ironic can talk back)
        as a kwarg.

        kwargs should have the following format:
        {
            'agent_url': 'http://AGENT_HOST:AGENT_PORT'
        }
                AGENT_PORT defaults to 9999.
        """
        node = task.node
        driver_info = node.driver_info
        LOG.info(_(
            'Heartbeat from %(node)s, last heartbeat at %(heartbeat)s.'),
            {'node': node.uuid,
             'heartbeat': driver_info.get('agent_last_heartbeat')})
        driver_info['agent_last_heartbeat'] = int(_time())
        driver_info['agent_url'] = kwargs['agent_url']
        node.driver_info = driver_info
        node.save(task.context)

        # Async call backs don't set error state on their own
        try:
            if node.provision_state in (states.DECOMMISSIONING,):
                msg = _('Could not finish tearing down node.')
                self._continue_tear_down(task, **kwargs)
            elif node.provision_state == states.DEPLOYWAIT:
                msg = _('Node failed to get image for deploy.')
                self._continue_deploy(task, **kwargs)
            elif (node.provision_state == states.DEPLOYING
                    and self._deploy_is_done(node)):
                msg = _('Node failed to move to active state.')
                self._reboot_to_instance(task, **kwargs)
            elif (node.provision_state == states.DEPLOYING
                    and not self._deploy_is_done(node)):
                return
        except Exception:
            LOG.exception('Async exception for %(node)s: %(msg)s',
                          {'node': node,
                           'msg': msg})
            _set_failed_state(task, msg)

    def _deploy_is_done(self, node):
        return self._client.deploy_is_done(node)

    def _continue_deploy(self, task, **kwargs):
        node = task.node
        node.provision_state = states.DEPLOYING
        node.save(task.context)

        LOG.info(_('Continuing deploy for %s'), node.uuid)

        image_source = node.instance_info.get('image_source')
        configdrive = node.instance_info.get('configdrive')

        # Get the swift temp url
        glance = image_service.Service(version=2, context=task.context)
        image_info = glance.show(image_source)
        swift_temp_url = glance.swift_temp_url(image_info)
        LOG.debug('Got image info: %(info)s for node %(node)s.',
                  {'info': image_info, 'node': node.uuid})
        image_info['urls'] = [swift_temp_url]

        # Tell the client to download and write the image with the given args
        res = self._client.prepare_image(node, image_info, configdrive)
        LOG.debug('prepare_image got response %(res)s for node %(node)s',
                  {'res': res, 'node': node})

    def _check_deploy_success(self, node):
        # should only ever be called after we've validated that
        # the prepare_image command is complete
        command = self._client.get_commands_status(node)[-1]
        if command['command_status'] == 'FAILED':
            return command['command_error']

    def _reboot_to_instance(self, task, **kwargs):
        node = task.node
        LOG.info(_('Preparing to reboot to instance for node %s'),
                 node.uuid)
        error = self._check_deploy_success(node)
        if error is not None:
            # TODO(jimrollenhagen) power off if using neutron dhcp to
            #                      align with pxe driver?
            LOG.error(_('node %(node)s command status errored: %(error)s'),
                      {'node': node.uuid,
                       'error': error})
            node.provision_state = states.DEPLOYFAIL
            node.target_provision_state = states.NOSTATE
            node.last_error = error
            node.save(task.context)
            return  # save error on node

        LOG.debug('Powering off node %s', node.uuid)
        manager_utils.node_power_action(task, states.POWER_OFF)
        # Remove provisioning network, commit public network
        LOG.debug('Switching to public network for node %s', node.uuid)
        neutron_client = _get_neutron_client(task.context)
        neutron_client.remove_provisioning_network(node)
        neutron_client.configure_instance_networks(node)

        LOG.info(_('Powering on node %s to finish deploying instance.'),
                 node.uuid)
        # TODO(jimrollenhagen) client.run_image(node)
        task.driver.ipmi_vendor._bmc_reset(task, warm=True)
        task.driver.ipmi_vendor._send_raw_bytes(task, '0x00 0x08 0x03 0x08')
        manager_utils.node_set_boot_device(task, 'disk', persistent=True)
        #NOTE(JoshNang) probably not needed. Testing before deleting.
        # task.driver.ipmi_vendor._set_bootparam(
        #     task, 'set bootflag force_disk')
        manager_utils.node_power_action(task, states.POWER_ON)

        node.provision_state = states.ACTIVE
        node.target_provision_state = states.NOSTATE
        node.save(task.context)

    def _continue_tear_down(self, task, **kwargs):
        # TODO(jimrollenhagen) decom things
        node = task.node
        # self._client.erase_drives()
        # For implementations which return DELETED from tear_down, the
        # ConductorManager automatically moves the node to NOSTATE. Because we
        # return DELETING and complete teardown in this method, we must
        # manually set NOSTATE instead of DELETED.
        node.provision_state = states.NOSTATE
        node.target_provision_state = states.NOSTATE
        node.save(task.context)

        LOG.info(_('Completed tear down for node %s'), node.uuid)

    def _lookup(self, context, **kwargs):
        """Method to be called the first time a ramdisk agent checks in. This
        can be because this is a node just entering decom or a node that
        rebooted for some reason. We will use the mac addresses listed in the
        kwargs to find the matching node, then return the node object to the
        agent. The agent can that use that UUID to use the normal vendor
        passthru method.

        Currently, we don't handle the instance where the agent doesn't have
        a matching node (i.e. a brand new, never been in Ironic node).

        kwargs should have one of the following formats:
        {
            hardware: [
                {
                    'id': 'aa:bb:cc:dd:ee:ff',
                    'type': 'mac_address'
                },
                ...
            ], ...
        }

        {
            version: "1",
            inventory: [
                {
                    'id': 'aa:bb:cc:dd:ee:ff',
                    'type': 'mac_address'
                },
                ...
            ], ...
        }

        {
            "version": "2"
            "inventory": {
                "interfaces": [
                    {
                        "name": "eth0",
                        "mac_address": "00:11:22:33:44:55",
                        "switch_port_descr": "port24"
                        "switch_chassis_descr": "tor1"
                    },
                    ...
                ], ...
            }
        }

        Originally, the agent did not send versioned hardware payloads.
        Unversioned payloads will be treated as v0 until they can be fully
        deprecated.

        The interfaces list should include a list of the non-IPMI MAC addresses
        in the form aa:bb:cc:dd:ee:ff.

        This method will also return the timeout for heartbeats. The driver
        will expect the agent to heartbeat before that timeout, or it will be
        considered down. This will be in a root level key called
        'heartbeat_timeout'

        :raises: NotFound if no matching node is found.
        :raises: InvalidParameterValue with unknown payload version
        """
        version = kwargs.get('version')

        if version not in self.supported_payload_versions:
            raise exception.InvalidParameterValue(_('Unknown lookup payload'
                                                    'version: %s') % version)
        interfaces = self._get_interfaces(version, kwargs)
        mac_addresses = self._get_mac_addresses(interfaces)

        node = self._find_node_by_macs(context, mac_addresses)

        LOG.info(_('Initial lookup for node %s succeeded.'), node.uuid)

        # Only support additional hardware in v2 and above. Grab all the
        # top level keys in inventory that aren't interfaces and add them.
        # Nest it in 'hardware' to avoid namespace issues
        hardware = {
            'hardware': {
                'network': interfaces
            }
        }
        if version >= 2:
            for key, value in kwargs.items():
                if key != 'interfaces':
                    hardware['hardware'][key] = value

        self._save_hardware(context, node, hardware, version)

        return {
            'heartbeat_timeout': CONF.agent.heartbeat_timeout,
            'node': node
        }

    def _initial_boot(self, task, node, **kwargs):
        """Agents need to be booted and check in before they'll be available
        to Nova to be deployed to, because we need the agents to LLDP so
        we know which ports they'll be attached to. After the first boot,
        they'll go in the decom -> boot -> decom cycle, and won't need this.
        """
        pass

    def _pre_cache(self, task, node, **kwargs):
        """Using some predetermined metric, decide which image should be
        pre-written to a certain node to make boot time speedier.
        """
        pass

    def _get_interfaces(self, version, inventory):
        # Coerce versions 0 and 1 to version 2
        interfaces = []
        if version in [None, '1']:

            if version is None:
                inventory_key = 'hardware'
            else:
                inventory_key = 'inventory'

            try:
                interface_list = inventory[inventory_key]
            except KeyError:
                raise exception.InvalidParameterValue(_(
                    'Malformed network interfaces lookup: %s') % inventory)

            for interface in interface_list:
                if interface.get('type') != 'mac_address':
                    continue
                if 'id' not in interface:
                    LOG.warning(_('Malformed MAC in hardware entry %s.'),
                                interface)
                    continue
                interfaces.append({
                    'mac_address': interface['id']
                })
        elif version == '2':
            try:
                interfaces = inventory['inventory']['interfaces']
            except (KeyError, TypeError):
                raise exception.InvalidParameterValue(_(
                    'Malformed network interfaces lookup: %s') % inventory)

        return interfaces

    def _get_mac_addresses(self, interfaces):
        """Returns MACs for the network devices
        """
        mac_addresses = []

        for interface in interfaces:
            try:
                mac_addresses.append(utils.validate_and_normalize_mac(
                    interface.get('mac_address')))
            except exception.InvalidMAC:
                LOG.warning(_('Malformed MAC: %s'), interface.get(
                    'mac_address'))
        return mac_addresses

    def _find_node_by_macs(self, context, mac_addresses):
        """Given a list of MAC addresses, find the ports that match the MACs
        and return the node they are all connected to.

        :raises: NodeNotFound if the ports point to multiple nodes or no
        nodes.
        """
        ports = self._find_ports_by_macs(context, mac_addresses)
        if not ports:
            raise exception.NodeNotFound(_(
                'No ports matching the given MAC addresses %sexist in the '
                'database.') % mac_addresses)
        node_id = self._get_node_id(ports)
        try:
            node = node_module.Node.get_by_id(context, node_id)
        except exception.NodeNotFound:
            with excutils.save_and_reraise_exception():
                LOG.exception(_('Could not find matching node for the '
                                'provided MACs %s.'), mac_addresses)

        return node

    def _find_ports_by_macs(self, context, mac_addresses):
        """Given a list of MAC addresses, find the ports that match the MACs
        and return them as a list of Port objects, or an empty list if there
        are no matches
        """
        ports = []
        for mac in mac_addresses:
            # Will do a search by mac if the mac isn't malformed
            try:
                # TODO(JoshNang) add port.get_by_mac() to Ironic
                # port.get_by_uuid() would technically work but shouldn't.
                port_ob = self.dbapi.get_port(port_id=mac)
                ports.append(port_ob)

            except exception.PortNotFound:
                LOG.warning(_('MAC address %s not found in database'), mac)

        return ports

    def _get_node_id(self, ports):
        """Given a list of ports, either return the node_id they all share or
        raise a NotFound if there are multiple node_ids, which indicates some
        ports are connected to one node and the remaining port(s) are connected
        to one or more other nodes.

        :raises: NodeNotFound if the MACs match multiple nodes. This
        could happen if you swapped a NIC from one server to another and
        don't notify Ironic about it or there is a MAC collision (since
        they're not guaranteed to be unique).
        """
        # See if all the ports point to the same node
        node_ids = set(port_ob.node_id for port_ob in ports)
        if len(node_ids) > 1:
            raise exception.NodeNotFound(_(
                'Ports matching mac addresses match multiple nodes. MACs: '
                '%(macs)s. Port ids: %(port_ids)s') %
                {'macs': [port_ob.address for port_ob in ports], 'port_ids':
                 [port_ob.uuid for port_ob in ports]}
            )

        # Only have one node_id left, return it.
        return node_ids.pop()

    def _save_hardware(self, context, node, hardware, version):
        """Flatten nested dict into namespace and save it to the node's
        extra field to avoid nested dictionaries.For example,
        {'interfaces': [{'mac_address': 'aa:bb:cc:dd:ee:ff'}]}}
        becomes {'hardware/interfaces/0/mac_address': 'aa:bb:cc:dd:ee:ff'}
        in the database.
        """
        flattened_dict = agent_utils.flatten_dict(hardware)
        extra = node['extra']
        extra.update(flattened_dict)
        node['extra'] = extra
        node.save(context)

    def _build_pxe_config_options(self, node):
        """Build the PXE config file for a node

        This method builds the PXE boot configuration file for a node,
        given all the required parameters.

        :param pxe_options: A dict of values to set on the configuration file
        :returns: A formatted string with the file content.
        """
        pxe_options = {
            'deployment_aki_path': CONF.agent.agent_kernel_path,
            'deployment_ari_path': CONF.agent.agent_initrd_path,
            'kernel_command_args': CONF.agent.agent_kernel_args,
            'ipa_api_url': '127.0.0.1',
            # Shouldn't need this.
            'ipa_advertise_host': ''
        }
        return tftp.build_pxe_config(node,
                                     pxe_options,
                                     CONF.agent.pxe_config_template)
