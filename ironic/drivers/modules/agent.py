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

import time

from oslo.config import cfg
from sqlalchemy.orm import exc

from ironic.common import exception
from ironic.common import image_service
from ironic.common import states
from ironic.common import utils
from ironic.conductor import utils as manager_utils
from ironic.db.sqlalchemy import api as dbapi
from ironic.drivers import base
from ironic.drivers.modules import agent_client
from ironic.objects import node
from ironic.openstack.common import log


"""States:

BUILDING: caching

DEPLOYING: applying instance definition (SSH pub keys, etc), rebooting
ACTIVE: ready to be used

DELETING: doing decom
DELETED: decom finished
"""


agent_driver_opts = [
    cfg.IntOpt('heartbeat_timeout',
                default=300,
                help='The length of time in seconds until the driver will '
                     'consider the agent down. The agent will attempt to '
                     'contact Ironic at some set fraction of this time '
                     '(defaulting to 2/3 the max time).'
                     'Defaults to 5 minutes.'),
]

CONF = cfg.CONF
CONF.register_opts(agent_driver_opts, group='agent_driver')

LOG = log.getLogger(__name__)


def _time():
    return time.time()


class AgentDeploy(base.DeployInterface):
    """Interface for deploy-related actions."""

    def _get_client(self):
        client = agent_client.AgentClient()
        return client

    def validate(self, task, node):
        """Validate the driver-specific git Node deployment info.

        This method validates whether the 'instance_info' property of the
        supplied node contains the required information for this driver to
        deploy images to the node.

        :param node: a single Node to validate.
        :raises: InvalidParameterValue
        """
        if node.instance_info is None:
            raise exception.InvalidParameterValue(_('instance_info cannot be '
                                                    'null.'))
        required_fields = ['agent_url', 'image_info', 'metadata', 'files']
        for field in required_fields:
            if field not in node.instance_info:
                raise exception.InvalidParameterValue(_('%s is required in '
                                                      'instance_info') %
                                                      field)

    def deploy(self, task, node):
        """Perform a deployment to a node.

        Perform the necessary work to deploy an image onto the specified node.
        This method will be called after prepare(), which may have already
        performed any preparatory steps, such as pre-caching some data for the
        node.

        :param task: a TaskManager instance.
        :param node: the Node to act upon.
        :returns: status of the deploy. One of ironic.common.states.
        """
        image_info = node.instance_info.get('image_info')
        metadata = node.instance_info.get('metadata')
        files = node.instance_info.get('files')

        # Get the swift temp url
        glance = image_service.Service(version=2)
        swift_temp_url = glance.swift_temp_url(image_info)
        image_info['urls'] = [swift_temp_url]

        # Tell the client to download and run the image with the given args
        client = self._get_client()
        client.prepare_image(node, image_info, metadata, files, wait=True)
        # TODO(JoshNang) Switch network here
        client.run_image(node, wait=True)
        # TODO(JoshNang) don't return until we have a totally working
        # machine, so we'll need to do some kind of testing here.
        return states.DEPLOYDONE

    def tear_down(self, task, node):
        """Reboot the machine and begin decom.

        When the node reboots, it will check in, see that it is supposed
        to be deleted, and start decom.

        :param task: a TaskManager instance.
        :param node: the Node to act upon.
        :returns: status of the deploy. One of ironic.common.states.
        """
        # Reboot
        manager_utils.node_power_action(task, node, states.REBOOT)
        # TODO(russell_h): resume decom when the agent comes back up
        return states.DELETING

    def prepare(self, task, node):
        """Prepare the deployment environment for this node.

        :param task: a TaskManager instance.
        :param node: the Node for which to prepare a deployment environment
                     on this Conductor.
        """
        # Not implemented. Try to keep as little state in the conductor as
        # possible.
        pass

    def clean_up(self, task, node):
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
        # Not implemented. tear_down does everything.
        pass

    def take_over(self, task, node):
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
        # Unnecessary. Trying to keep everything as stateless as possible.
        pass


class AgentVendorInterface(base.VendorInterface):
    def __init__(self):
        self.vendor_routes = {
            'heartbeat': self._heartbeat
        }
        self.driver_routes = {
            'lookup': self._heartbeat_no_uuid
        }
        self.db_connection = dbapi.get_backend()

    def validate(self, task, node, method, *args, **kwargs):
        """Validate the driver-specific Node deployment info.

        This method validates whether the 'instance_info' property of the
        supplied node contains the required information for this driver to
        deploy images to the node.

        :param node: a single Node to validate.
        :raises: InvalidParameterValue
        """
        if 'agent_url' not in node.instance_info:
            raise exception.InvalidParameterValue(_('agent_url is required to'
                                                    ' talk to the agent'))

    def driver_vendor_passthru(self, task, method, **kwargs):
        """A node that does not know its UUID should POST to this method.
        Given method, route the command to the appropriate private function.
        """
        if method not in self.driver_routes:
            raise exception.InvalidParameterValue(_('No handler for method %s')
                                                  % method)
        func = self.driver_routes[method]
        return func(task, **kwargs)

    def vendor_passthru(self, task, node, **kwargs):
        """A node that knows its UUID should heartbeat to this passthru. It
        will get its node object back, with what Ironic thinks its provision
        state is and the target provision state is.
        """
        method = kwargs['method']  # Existence checked in mixin
        if method not in self.vendor_routes:
            raise exception.InvalidParameterValue(_('No handler for method '
                                                    '%s') % method)
        func = self.vendor_routes[method]
        return func(task, node, **kwargs)

    def _heartbeat(self, task, node, **kwargs):
        """Method for agent to periodically check in. The agent should be
        sending its agent_url (so Ironic can talk back) as a kwarg.

        kwargs should have the following format:
        {
            'agent_url': 'http://AGENT_HOST:AGENT_PORT'
        }
                AGENT_PORT defaults to 9999.
        """
        if 'agent_url' not in kwargs:
            raise exception.InvalidParameterValue(_('"agent_url" is a required'
                                                    ' parameter'))
        instance_info = node.instance_info
        instance_info['last_heartbeat'] = int(_time())
        instance_info['agent_url'] = kwargs['agent_url']
        node.instance_info = instance_info
        node.save(task.context)
        return node

    def _heartbeat_no_uuid(self, context, **kwargs):
        """Method to be called the first time a ramdisk agent checks in. This
        can be because this is a node just entering decom or a node that
        rebooted for some reason. We will use the mac addresses listed in the
        kwargs to find the matching node, then return the node object to the
        agent. The agent can that use that UUID to use the normal vendor
        passthru method.

        Currently, we don't handle the instance where the agent doesn't have
        a matching node (i.e. a brand new, never been in Ironic node).

        kwargs should have the following format:
        {
            hardware: [
                {
                    'id': 'aa:bb:cc:dd:ee:ff',
                    'type': 'mac_address'
                },
                {
                    'id': '00:11:22:33:44:55',
                    'type': 'mac_address'
                }
            ], ...
        }

        hardware is a list of dicts with id being the actual mac address,
        with type 'mac_address' for the non-IPMI ports in the
        server, (the normal network ports). They should be in the format
        "aa:bb:cc:dd:ee:ff".

        This method will also return the timeout for heartbeats. The driver
        will expect the agent to heartbeat before that timeout, or it will be
        considered down. This will be in a root level key called
        'heartbeat_timeout'
        """
        if 'hardware' not in kwargs or not kwargs['hardware']:
            raise exception.InvalidParameterValue(_('"hardware" is a '
                                                    'required parameter and '
                                                    'must not be empty'))

        # Find the address from the hardware list
        mac_addresses = []
        for hardware in kwargs['hardware']:
            if 'id' not in hardware or 'type' not in hardware:
                LOG.warning(_('Malformed hardware entry %s') % hardware)
                continue
            if hardware['type'] == 'mac_address':
                try:
                    mac = utils.validate_and_normalize_mac(
                        hardware['id'])
                except exception.InvalidMAC:
                    LOG.warning(_('Malformed MAC in hardware entry %s.')
                                % hardware)
                    continue
                mac_addresses.append(mac)

        node_object = self._find_node_by_macs(context, mac_addresses)
        return {
            'heartbeat_timeout': CONF.agent_driver.heartbeat_timeout,
            'node': node_object
        }

    def _find_node_by_macs(self, context, mac_addresses):
        """Given a list of MAC addresses, find the ports that match the MACs
        and return the node they are all connected to.

        :raises: NodeNotFound if the ports point to multiple nodes or no
        nodes.
        """
        ports = self._find_ports_by_macs(mac_addresses)
        if not ports:
            raise exception.NodeNotFound(_('No ports matching the given MAC '
                                           'addresses exist in the database.'))
        node_id = self._get_node_id(ports)
        try:
            node_object = node.Node.get_by_uuid(context, node_id)
        except exc.NoResultFound:
            LOG.exception(_('Could not find matching node for the '
                            'provided MACs.'))
            raise exception.NodeNotFound(_('Could not find matching node for '
                                           'the provided MACs.'))
        return node_object

    def _find_ports_by_macs(self, mac_addresses):
        """Given a list of MAC addresses, find the ports that match the MACs
        and return them as a list of Port objects.

        :raises: NotFound if the no matching ports are found.
        """
        ports = []
        for mac in mac_addresses:
            # Will do a search by mac if the mac isn't malformed
            try:
                # TODO(JoshNang) add port.get_by_mac() to Ironic
                # port.get_by_uuid() would technically work but shouldn't.
                port = self.db_connection.get_port(port_id=mac)
                ports.append(port)

            except exception.PortNotFound:
                LOG.warning(_('MAC address %s not found in '
                              'database') % mac)

        return ports

    def _get_node_id(self, ports):
        """Given a list of ports, either return the node_id they all share or
        raise a NotFound if there are multiple node_ids (indicating some
        ports are connected to one node and other ports are connected to
        different nodes in the DB

        :raises: NodeNotFound if the MACs match multiple nodes. This
        could happen if you swapped a NIC from one server to another and
        don't notify Ironic about it or there is a MAC collision (since
        they're not guaranteed to be unique).
        """
        # See if all the ports point to the same node
        node_ids = set(port.node_id for port in ports)
        if len(node_ids) > 1:
            raise exception.NodeNotFound(_('Ports matching mac addresses '
                                           'match multiple nodes.'))

        # Only have one node_id left, return it.
        return node_ids.pop()
