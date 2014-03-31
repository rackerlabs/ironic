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

import mock

from ironic.drivers.modules import agent_client
from ironic.tests import base


class MockResponse(object):
    def __init__(self, data):
        self.data = data
        self.text = json.dumps(data)

    def json(self):
        return self.data


class MockNode(object):
    def __init__(self):
        self.driver_info = {
            'agent_url': "http://127.0.0.1:9999"
        }


class TestAgentClient(base.TestCase):
    def setUp(self):
        super(TestAgentClient, self).setUp()
        self.client = agent_client.AgentClient()
        self.client.session = mock.Mock(autospec=requests.Session)
        self.node = MockNode()

    @mock.patch('uuid.uuid4', mock.MagicMock(return_value='uuid'))
    def test_cache_image(self):
        self.client._command = mock.Mock()
        image_info = {'image_id': 'image'}
        params = {
            'image_info': image_info,
            'force': False,
        }

        self.client.cache_image(self.node, image_info)
        self.client._command.assert_called_once_with(node=self.node,
                                         method='standby.cache_image',
                                         params=params,
                                         wait=False)

    @mock.patch('uuid.uuid4', mock.MagicMock(return_value='uuid'))
    def test_prepare_image(self):
        self.client._command = mock.Mock()
        image_info = {'image_id': 'image'}
        configdrive = {}
        params = {
            'image_info': image_info,
            'configdrive': configdrive,
        }

        self.client.prepare_image(self.node,
                                  image_info,
                                  configdrive,
                                  wait=False)
        self.client._command.assert_called_once_with(node=self.node,
                                         method='standby.prepare_image',
                                         params=params,
                                         wait=False)

    @mock.patch('uuid.uuid4', mock.MagicMock(return_value='uuid'))
    def test_run_image(self):
        self.client._command = mock.Mock()
        params = {}

        self.client.run_image(self.node)
        self.client._command.assert_called_once_with(node=self.node,
                                         method='standby.run_image',
                                         params=params,
                                         wait=False)

    def test_secure_drives(self):
        self.client._command = mock.Mock()
        key = 'lol'
        drives = ['/dev/sda']
        params = {'key': key, 'drives': drives}

        self.client.secure_drives(self.node, drives, key)
        self.client._command.assert_called_once_with(node=self.node,
                                         method='decom.secure_drives',
                                         params=params,
                                         wait=False)

    def test_erase_drives(self):
        self.client._command = mock.Mock()
        key = 'lol'
        drives = ['/dev/sda']
        params = {'key': key, 'drives': drives}

        self.client.erase_drives(self.node, drives, key)
        self.client._command.assert_called_once_with(node=self.node,
                                         method='decom.erase_drives',
                                         params=params,
                                         wait=False)

    def test_command(self):
        response_data = {'status': 'ok'}
        self.client.session.post.return_value = MockResponse(response_data)
        method = 'standby.run_image'
        image_info = {'image_id': 'test_image'}
        params = {'image_info': image_info}

        url = self.client._get_command_url(self.node)
        body = self.client._get_command_body(method, params)
        headers = {'Content-Type': 'application/json'}

        response = self.client._command(self.node, method, params)
        self.assertEqual(response, response_data)
        self.client.session.post.assert_called_once_with(
            url,
            data=body,
            headers=headers,
            params={'wait': 'false'})
