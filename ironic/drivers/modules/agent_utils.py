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

from ironic.common import neutron
from ironic.db.sqlalchemy import api as db_api


agent_opts = [
    cfg.IntOpt('provisioning_network_uuid',
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
    def _get_node_lldp(self, node):
        """Unflatten the hardware dictionary and extract the LLDP
        information
        """
        for k, v in node['extra'].items():
            pass

    def remove_provisioning_network(self):
        """Remove the provisioning server from the node."""
        self.client.delete_port(CONF.agent.provisioning_network_uuid)

    def add_provisioning_network(self, node):
        lldp = self._get_node_lldp(node)
        params = {
            'switch:portmaps': lldp,
            'switch:hardware_id': node.uuid,
            'switch:commit': True,
            'network_id': CONF.agent.provisioning_network_uuid
        }
        self.client.create_port(params)

    def add_public_network(self, node):
        # Remove public network is handled by Nova during destroy()
        db_backend = db_api.get_backend()
        for port in db_backend.get_ports_by_node_id(self, node.id):
            lldp = self._get_node_lldp(node)
            params = {
                'switch:portmaps': lldp,
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

    # TODO(JoshNang) generalize and test this. Just for testing neutron
    # quick and dirty testing
    dictionary = {'interfaces': [{}, {}]}
    for k, v in flattened.items():
        (_, index, key) = k.split(seperator)
        dictionary['interfaces'][int(index)][key] = v
    return dictionary
