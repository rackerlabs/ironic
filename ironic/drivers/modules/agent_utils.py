#
# Copyright 2014 Rackspace, Inc.
# All Rights Reserved
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

from neutronclient.common import exceptions as neutron_exceptions
from oslo.config import cfg

from ironic.common import exception
from ironic.common import neutron
from ironic.db import api as dbapi
from ironic.openstack.common import log

agent_opts = [
    cfg.StrOpt('provisioning_network_uuid',
               help='The uuid of the provisioning network, where the agent '
                    'lives.'),
]

LOG = log.getLogger(__name__)

CONF = cfg.CONF
CONF.register_opts(agent_opts, group='agent')


class AgentNeutronAPI(neutron.NeutronAPI):
    """API for communicating with neutron 2.x API using the Ironic Neutron
    Plugin located here: https://github.com/rackerlabs/ironic-neutron-plugin
    """
    def __init__(self, *args, **kwargs):
        super(AgentNeutronAPI, self).__init__(*args, **kwargs)
        self.dbapi = dbapi.get_instance()

    def _get_node_portmap(self, node):
        """Extract the switch port information for the node."""
        unflattened_extra = unflatten_dict(node.extra)
        if (unflattened_extra.get('hardware') and
                unflattened_extra['hardware'].get('interfaces')):
            portmap = []
            interfaces = unflattened_extra['hardware'].get('interfaces')
            for index, interface in enumerate(interfaces):
                portmap.append({
                    'switch_id': interface['switch_chassis_id'].lower(),
                    'port': interface['switch_port_id'].lower(),
                    'name': interface.get('name') or 'eth%d' % index
                })
            return portmap
        else:
            raise exception.InvalidParameterValue(_(
                'Could not get interface info out of node\'s extra field.'))

    def add_provisioning_network(self, node):
        """Add the provisioning network to the node."""
        # Make sure the node is not already in the provisioning network
        params = {
            'switch:hardware_id': node.uuid,
        }
        ports = self.client.list_ports(**params).get('ports')
        for port in ports:
            if port['network_id'] == CONF.agent.provisioning_network_uuid:
                if (port.get('switch:ports') and
                        port['switch:ports'].get('commit')):
                    LOG.info('Node %s is already on provisioning network',
                              node.uuid)
                    return
                else:
                    LOG.debug('Node %s has provisioning network but is not '
                            'committed.', node.uuid)
        LOG.info(_('Adding the provisioning network for %s'), node.uuid)
        portmap = self._get_node_portmap(node)
        if not portmap:
            raise exception.NoValidPortmaps(
                node=node.uuid, vif=CONF.agent.provisioning_network_uuid)
        body = {
            'port': {
                'switch:ports': portmap,
                'switch:hardware_id': node.uuid,
                'commit': True,
                'trunked': False,
                'network_id': CONF.agent.provisioning_network_uuid,
                # TODO(JoshNang) remove when neutron plugin gets fixed
                'tenant_id': 'fake'
            }
        }

        try:
            self.client.create_port(body)
        except neutron_exceptions.ConnectionFailed:
            raise exception.NetworkError(_(
                'Could not remove provisioning network %(vif)s '
                'from %(node)s') %
                {'vif': CONF.agent.provisioning_network_uuid,
                 'node': node.uuid})

    def remove_provisioning_network(self, node):
        """Remove the provisioning network from the node."""
        LOG.info(_('Removing the provisioning network for %s'), node.uuid)
        params = {
            'switch:hardware_id': node.uuid,
            'network_id': CONF.agent.provisioning_network_uuid
        }
        try:
            ports = self.client.list_ports(**params)
        except neutron_exceptions.ConnectionFailed:
            raise exception.NetworkError(_(
                'Could not get provisioning network vif '
                'for %s from Neutron, possible network issue.') % node.uuid)

        if not ports or not ports.get('ports'):
            LOG.warning(_('No provisioning network ports '
                          'attached to node %(node)s. got: %(ret)s'),
                        {'node': node.uuid, 'ret': ports})
            return
        if len(ports['ports']) > 1:
            LOG.warning(_('Multiple provisioning networks found for node %s, '
                          'attempting to remove all of them'),
                        node.uuid)
        for network in ports['ports']:
            try:
                self.client.delete_port(network.get('id'))
            except neutron_exceptions.ConnectionFailed:
                raise exception.NetworkError(_(
                    'Could not remove provisioning network %(vif)s '
                    'from %(node)s, possible network issue.') %
                    {'vif': CONF.agent.provisioning_network_uuid,
                     'node': node.uuid})

    def configure_instance_networks(self, node):
        """Commit the configured network for the node to the switch."""
        # Remove public network is handled by Nova during destroy()
        LOG.info(_('Mapping instance ports to %s'), node.uuid)
        portmap = self._get_node_portmap(node)
        if not portmap:
            raise exception.NoValidPortmaps(
                node=node.uuid, vif=CONF.agent.provisioning_network_uuid)
        # TODO(russell_h): this is based on the broken assumption that the
        # number of Neutron ports will match the number of physical ports.
        # Instead, we should probably list ports for this this instance in
        # Neutron and update all of those with the appropriate portmap.
        ports = self.dbapi.get_ports_by_node_id(node.id)
        if not ports:
            raise exception.NetworkError(_(
                "No public network ports to activate attached to "
                "node %s") % node.uuid)
        for port in ports:
            vif_port_id = port['extra'].get('vif_port_id')
            LOG.debug('Mapping instance port %(vif_port_id)s to node '
                      '%(node_id)s',
                      {'vif_port_id': vif_port_id, 'node_id': node.id})
            body = {
                'port': {
                    'switch:ports': portmap,
                    'switch:hardware_id': node.uuid,
                    'commit': True,
                    'trunked': True
                }
            }
            if not port['extra'].get('vif_port_id'):
                LOG.error('Node %(node)s port has no vif id in extra: %s',
                          {'extra': port['extra'], 'node': node.uuid})
                continue
            try:
                self.client.update_port(port['extra'].get(
                    'vif_port_id'), body)
            except neutron_exceptions.ConnectionFailed:
                raise exception.NetworkError(_(
                    'Could not add public network %(vif)s '
                    'to %(node)s, possible network issue.') %
                    {'vif': port['extra'].get('vif_port_id'),
                     'node': node.uuid})

    def deconfigure_instance_networks(self, node):
        """Remove the provisioning server from the node."""
        # Unmap the Neutron ports from the physical ones, but leave them
        # around for Nova.
        LOG.info(_('Unmapping instance ports from %s'), node.uuid)

        # TODO(russell_h): same problem as in configure_instance_networks, this
        # is based on the broken assumption that the number of Neutron ports
        # will match the number of physical ports.
        params = {
            'switch:hardware_id': node.uuid,
        }
        ports = self.client.list_ports(**params).get('ports')
        if not ports:
            raise exception.NetworkError(_(
                "No public network ports to deactivate attached to "
                "node %s") % node.uuid)
        for port in ports:
            if port['network_id'] == CONF.agent.provisioning_network_uuid:
                # Don't delete the provisioning network here
                continue
            LOG.debug('Unmapping instance port %(vif_port_id)s from node '
                      '%(node_id)s',
                      {'vif_port_id': port['id'], 'node_id': node.uuid})
            body = {
                'port': {
                    'switch:ports': [],
                    'switch:hardware_id': node.uuid,
                    'commit': False
                }

            }
            try:
                self.client.update_port(port['id'], body)
            except neutron_exceptions.ConnectionFailed:
                raise exception.NetworkError(_(
                    'Could not remove public network %(vif)s from %(node)s, '
                    'possible network issue.') %
                    {'vif': port['id'],
                     'node': node.uuid})


def flatten_dict(item, path='', separator='/', flattened=None):
    """Do a depth first search through the 'tree' of the dictionary
    to flatten it out. Turns {'a': {'b': ['c', 'd']}} into
    {'a/b/0': 'c', 'a/b/1': 'd'}

    :param item: The dictionary to be flattened, or the current node
    in the tree to examine in the recursive call.
    :param path: The path so far, separated by separator towards the leaf.
    :param separator: The character to separate keys in the database.
    :param flattened: Variable used by recursive calls. Leave as None for
    initial call to flatten_dict.
    """
    if flattened is None:
        flattened = {}
    # Base Case
    if isinstance(item, str):
        flattened[path] = item
        return flattened
    elif isinstance(item, int):
        flattened[path] = str(item)
        return flattened
    else:
        # Recursive Case

        # Avoid leading slash
        if path:
            path += separator

        if isinstance(item, dict):
            for k, v in item.items():
                new_path = path + k
                flatten_dict(v, new_path, separator, flattened)
            return flattened
        elif isinstance(item, list):
            # Add an index, so we get interfaces/0/name: eth0a
            for index, v in enumerate(item):
                new_path = path + str(index)
                flatten_dict(v, new_path, separator, flattened)
            return flattened


def unflatten_dict(flattened, separator='/'):
    """Given a dictionary flattened by flatten_dict, return the original
    dictionary structure.

    Uses lookahead iteration to account for lists.
    :param flattened: A flattened dictionary to be unflattened.
    :param separator: The character separating keys in the flattened
    dictionary.
    """
    unflattened = {}
    for k, v in flattened.items():
        keys = k.split(separator)
        sub_item = unflattened
        parent_key = None
        parent_item = None
        for index, key in enumerate(keys):
            try:
                # List
                index = int(key)
            except ValueError:
                # Dict
                if index == len(keys) - 1:
                    sub_item[key] = v
                else:
                    parent_item = sub_item
                    sub_item = sub_item.setdefault(key, {})
                    parent_key = key
                continue

            # See if we need to create the array
            if not parent_item.get(parent_key):
                parent_item[parent_key] = []
            array = parent_item[parent_key]

            # Index out of range
            if index >= len(array):
                # array += [{}] * (index - len(array) + 1)
                array += [{} for _ in range(index - len(array) + 1)]

            # Set the item in the array
            parent_item = sub_item
            sub_item = array[index]
            parent_key = key

    return unflattened
