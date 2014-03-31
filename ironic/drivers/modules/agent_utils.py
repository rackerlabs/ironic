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

from oslo.config import cfg

from ironic.common import exception
from ironic.common import neutron
from ironic.db import api as dbapi
from ironic.openstack.common import log

agent_opts = [
    cfg.StrOpt('provisioning_network_uuid',
                help='The uuid of the provisioning network, where the agent '
                     'lives.'
    )
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
        """Unflatten the hardware dictionary and extract the LLDP
        information
        """
        unflattened_extra = unflatten_dict(node.extra)
        if (unflattened_extra.get('hardware') and
                unflattened_extra['hardware'].get('interfaces')):
            portmap = []
            interfaces = unflattened_extra['hardware'].get('interfaces')
            for interface in interfaces:
                portmap.append({
                    'system_name': interface['switch_chassis_id'].lower(),
                    'port_id': interface['switch_port_id'].lower()
                })
            return portmap
        else:
            raise exception.InvalidParameterValue(_(
                'Could not get interface info out of node\'s extra field.'))

    def add_provisioning_network(self, node):
        LOG.info(_('Adding the provisioning network for %s') % node.uuid)
        portmap = self._get_node_portmap(node)
        params = {
            'switch:portmaps': portmap,
            'switch:hardware_id': node.uuid,
            'switch:commit': True,
            'network_id': CONF.agent.provisioning_network_uuid
        }

        self.client.create_port(params)

    def remove_provisioning_network(self, node):
        """Remove the provisioning server from the node."""
        LOG.info(_('Removing the provisioning network for %s') % node.uuid)
        params = {
            'switch:hardware_id': node.uuid,
            'network_id': CONF.agent.provisioning_network_uuid
        }
        ports = self.client.list_ports(params=params)
        for port in ports:
            self.client.delete_port(port)

    def add_public_network(self, node):
        # Remove public network is handled by Nova during destroy()
        LOG.info(_('Adding public network for %s') % node.uuid)
        for port in self.dbapi.get_ports_by_node_id(self, node.id):
            portmap = self._get_node_portmap(node)
            params = {
                'switch:portmaps': portmap,
                'switch:hardware_id': node.uuid,
                'switch:commit': True
            }
            self.client.update_port(port['extra'].get('vif_port_id'), params)

    def remove_public_network(self, node):
        """Remove the provisioning server from the node."""
        LOG.info(_('Removing the provisioning network for %s') % node.uuid)
        for port in self.dbapi.get_ports_by_node_id(self, node.id):
            vif = port.extra.get('vif_port_id')
            self.client.delete_port(vif)
            # Delete the ports so Nova doesn't try to delete them as well.
            self.dbapi.destroy_port(port.id)


def flatten_dict(item, path='', seperator='/', flattened=None):
    """Do a depth first search through the 'tree' of the dictionary
    to flatten it out. Turns {'a': {'b': ['c', 'd']}} into
    {'a/b/0': 'c', 'a/b/1': 'd'}

    :param item: The dictionary to be flattened, or the current node
    in the tree to examine in the recursive call
    :param path: The path so far, separated by '/' towards the leaf.
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
            path += seperator
        else:
            path = ''

        if isinstance(item, dict):
            for k, v in item.items():
                new_path = path + k
                flatten_dict(v, new_path, seperator, flattened)
            return flattened
        elif isinstance(item, list):
            # Add an index, so we get interfaces/0/name: eth0a
            for index, v in enumerate(item):
                new_path = path + str(index)
                flatten_dict(v, new_path, seperator, flattened)
            return flattened


def unflatten_dict(flattened, seperator='/'):
    """Given a dictionary flattened by flatten_dict, return the original
    dictionary structure.

    Uses lookahead iteration to account for lists.
    """
    unflattened = {}
    for k, v in flattened.items():
        keys = k.split(seperator)
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
                array += [{}] * (index - len(array) + 1)

            # Set the item in the array
            parent_item = sub_item
            sub_item = array[index]
            parent_key = key

    return unflattened
