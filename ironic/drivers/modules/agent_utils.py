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


agent_opts = [
    cfg.StrOpt('provisioning_network_uuid',
                help='The uuid of the provisioning network, where the agent '
                     'lives.'
    )
]

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

    def remove_provisioning_network(self, node):
        """Remove the provisioning server from the node."""
        self.client.delete_port(node, CONF.agent.provisioning_network_uuid)

    def add_provisioning_network(self, node):
        portmap = self._get_node_portmap(node)
        params = {
            'switch:portmaps': portmap,
            'switch:hardware_id': node.uuid,
            'switch:commit': True,
            'network_id': CONF.agent.provisioning_network_uuid
        }
        port_data = self.client.create_port(params)
        #TODO(JoshNang) save port so we can destroy it when machine reboots

    def add_public_network(self, node):
        # Remove public network is handled by Nova during destroy()
        for port in self.dbapi.get_ports_by_node_id(self, node.id):
            portmap = self._get_node_portmap(node)
            params = {
                'switch:portmaps': portmap,
                'switch:hardware_id': node.uuid,
                'switch:commit': True
            }
            self.client.update_port(port['extra'].get('vif_port_id'), params)


def flatten_dict(item, path='', seperator='/', flattened={}):
    """Do a depth first search through the 'tree' of the dictionary
    to flatten it out. Turns {'a': {'b': ['c', 'd']}} into
    {'a/b/0': 'c', 'a/b/1': 'd'}

    :param item: The dictionary to be flattened, or the current node
    in the tree to examine in the recursive call
    :param path: The path so far, separated by '/' towards the leaf.
    """
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
    """
    unflattened = {}
    for k, v in flattened.items():
        keys = k.split(seperator)
        sub_item = unflattened
        parent_key = keys[0]
        for index, key in enumerate(keys[1:]):
            try:
                index = int(key)
                array = sub_item.setdefault(parent_key, [None] * (index + 1))
                if not array[index]:
                    array[index] = {}
                sub_item = array[index]
                parent_key = key
            except ValueError:
                if index == len(keys) - 2:
                    sub_item[key] = v
                else:
                    sub_item = sub_item.setdefault(key, {})
                    parent_key = key
    return unflattened
