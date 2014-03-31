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

import json
import requests

from ironic.common import exception
from ironic.openstack.common import jsonutils
from ironic.openstack.common import log


class AgentClient(object):
    """Client for interacting with nodes via a REST API."""
    def __init__(self):
        self.session = requests.Session()
        self.log = log.getLogger(__name__)

    def _get_command_url(self, node):
        if 'agent_url' not in node.instance_info:
            raise exception.IronicException(_('Agent driver requires '
                                              'agent_url'))
        return '{0}/v1.0/commands'.format(node.instance_info['agent_url'])

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
        return json.loads(response.text)

    def cache_image(self, node, image_info, force=False, wait=False):
        """Attempt to cache the specified image."""
        self.log.debug(_('Caching image %(image)s on node %(node)s.') %
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

    def prepare_image(self, node, image_info, metadata, files, wait=False):
        """Call the `prepare_image` method on the node."""
        self.log.debug(_('Preparing image %(image)s on node %(node)s.') %
                       {'image': image_info.get('image_id'),
                        'node': self._get_command_url(node)})
        return self._command(node=node,
                             method='standby.prepare_image',
                             params={
                                 'image_info': image_info,
                                 'metadata': metadata,
                                 'files': files,
                             },
                             wait=wait)

    def run_image(self, node, wait=False):
        """Run the specified image."""
        self.log.debug(_('Running image on node %s.') %
                        (self._get_command_url(node)))
        return self._command(node=node,
                             method='standby.run_image',
                             params={},
                             wait=wait)

    def secure_drives(self, node, drives, key, wait=False):
        """Secures given drives with given key."""
        self.log.info('Securing drives %s for node %s' %
                      (drives, self._get_command_url(node)))
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
        self.log.info('Erasing drives %s for node %s' %
                      (drives, self._get_command_url(node)))
        params = {
            'drives': drives,
            'key': key,
        }
        return self._command(node=node,
                             method='decom.erase_drives',
                             params=params,
                             wait=wait)
