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

from oslo.config import cfg
import requests

from ironic.common import exception
from ironic.openstack.common import jsonutils
from ironic.openstack.common import log

agent_opts = [
    cfg.StrOpt('agent_api_version',
               default='v1',
               help='API version to communicate with the ramdisk agent over.')
]

CONF = cfg.CONF
CONF.register_opts(agent_opts, group='agent')

LOG = log.getLogger(__name__)


class AgentClient(object):
    """Client for interacting with nodes via a REST API."""
    def __init__(self):
        self.session = requests.Session()

    def _get_command_url(self, node):
        if 'agent_url' not in node.driver_info:
            raise exception.IronicException(_('Agent driver requires '
                                              'agent_url in driver_info'))
        return ('%(agent_url)s/%(api_version)s/commands' %
                {'agent_url': node.driver_info['agent_url'],
                 'api_version': CONF.agent.agent_api_version})

    def _get_command_body(self, method, params):
        return jsonutils.dumps({
            'name': method,
            'params': params,
        })

    def _command(self, node, method, params, wait=False):
        url = self._get_command_url(node)
        body = self._get_command_body(method, params)
        request_params = {
            'wait': str(wait).lower()
        }
        headers = {
            'Content-Type': 'application/json'
        }
        response = self.session.post(url,
                                     params=request_params,
                                     data=body,
                                     headers=headers)

        # TODO(russellhaering): real error handling
        return response.json()

    def get_commands_status(self, node):
        url = self._get_command_url(node)
        headers = {'Content-Type': 'application/json'}
        res = self.session.get(url, headers=headers)
        return res.json()['commands']

    def deploy_is_done(self, node):
        commands = self.get_commands_status(node)
        if not commands:
            return False

        last_command = commands[-1]

        if last_command['command_name'] != 'prepare_image':
            # catches race condition where prepare_image is still processing
            # so deploy hasn't started yet
            return False

        if last_command['command_status'] != 'RUNNING':
            return True

        return False

    def cache_image(self, node, image_info, force=False, wait=False):
        """Attempt to cache the specified image."""
        LOG.debug('Caching image %(image)s on node %(node)s.',
                  {'image': image_info.get('image_id'),
                   'node': self._get_command_url(node)})
        params = {
            'image_info': image_info,
            'force': force
        }
        return self._command(node=node,
                             method='standby.cache_image',
                             params=params,
                             wait=wait)

    def prepare_image(self, node, image_info, configdrive, wait=False):
        """Call the `prepare_image` method on the node."""
        LOG.debug('Preparing image %(image)s on node %(node)s.',
                  {'image': image_info.get('id'),
                   'node': self._get_command_url(node)})
        return self._command(node=node,
                             method='standby.prepare_image',
                             params={
                                 'image_info': image_info,
                                 'configdrive': configdrive,
                             },
                             wait=wait)

    def run_image(self, node, wait=False):
        """Run the specified image."""
        LOG.debug('Running image on node %s.',
                  self._get_command_url(node))
        return self._command(node=node,
                             method='standby.run_image',
                             params={},
                             wait=wait)

    def secure_drives(self, node, drives, key, wait=False):
        """Secures given drives with given key."""
        LOG.info('Securing drives %(drives)s for node %(node)s',
                 {'drives': drives,
                  'node': self._get_command_url(node)})
        params = {
            'drives': drives,
            'key': key,
        }
        return self._command(node=node,
                             method='decom.secure_drives',
                             params=params,
                             wait=wait)

    def erase_drives(self, node, drives, key, wait=False):
        """Erases given drives."""
        LOG.info('Erasing drives %(drives)s for node %(node)s',
                 {'drives': drives,
                  'node': self._get_command_url(node)})
        params = {
            'drives': drives,
            'key': key,
        }
        return self._command(node=node,
                             method='decom.erase_drives',
                             params=params,
                             wait=wait)
