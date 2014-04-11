# Copyright 2013 Hewlett-Packard Development Company, L.P.
# All Rights Reserved.
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

import mock

from ironic.common import driver_factory
from ironic.common import exception
from ironic.conductor import task_manager
from ironic.db import api as db_api
from ironic.drivers.modules import fake
from ironic.drivers import utils as driver_utils
from ironic.openstack.common import context
from ironic.tests import base
from ironic.tests.conductor import utils as mgr_utils
from ironic.tests.db import utils as db_utils


class UtilsTestCase(base.TestCase):

    def setUp(self):
        super(UtilsTestCase, self).setUp()
        self.context = context.get_admin_context()
        self.dbapi = db_api.get_instance()
        mgr_utils.mock_the_extension_manager()
        self.driver = driver_factory.get_driver("fake")
        ndict = db_utils.get_test_node()
        self.node = self.dbapi.create_node(ndict)

    @mock.patch.object(fake.FakeVendorA, 'validate')
    def test_vendor_interface_validate_valid_methods(self,
                                                     mock_fakea_validate):
        self.driver.vendor.validate(method='first_method')
        mock_fakea_validate.assert_called_once_with(method='first_method')

    def test_vendor_interface_validate_bad_method(self):
        self.assertRaises(exception.UnsupportedDriverExtension,
                          self.driver.vendor.validate, method='fake_method')

    def test_vendor_interface_validate_none_method(self):
        self.assertRaises(exception.InvalidParameterValue,
                          self.driver.vendor.validate)

    @mock.patch.object(fake.FakeVendorA, 'vendor_passthru')
    @mock.patch.object(fake.FakeVendorB, 'vendor_passthru')
    def test_vendor_interface_route_valid_method(self, mock_fakeb_vendor,
                                                 mock_fakea_vendor):
        self.driver.vendor.vendor_passthru('task', 'node',
                                           method='first_method',
                                           param1='fake1', param2='fake2')
        mock_fakea_vendor.assert_called_once_with('task',
                                            'node',
                                            method='first_method',
                                            param1='fake1', param2='fake2')
        self.driver.vendor.vendor_passthru('task', 'node',
                                           method='second_method',
                                           param1='fake1', param2='fake2')
        mock_fakeb_vendor.assert_called_once_with('task',
                                            'node',
                                            method='second_method',
                                            param1='fake1', param2='fake2')

    def test_driver_passthru_mixin_success(self):
        vendor_a = fake.FakeVendorA()
        vendor_a.driver_vendor_passthru = mock.Mock()
        vendor_b = fake.FakeVendorB()
        vendor_b.driver_vendor_passthru = mock.Mock()
        driver_vendor_mapping = {
            'method_a': vendor_a,
            'method_b': vendor_b,
        }
        mixed_vendor = driver_utils.MixinVendorInterface(
            {},
            driver_vendor_mapping)
        mixed_vendor.driver_vendor_passthru('context',
                                            'method_a',
                                            param1='p1')
        vendor_a.driver_vendor_passthru.assert_called_once_with(
            'context',
            'method_a',
            param1='p1')

    def test_driver_passthru_mixin_unsupported(self):
        mixed_vendor = driver_utils.MixinVendorInterface({}, {})
        self.assertRaises(exception.UnsupportedDriverExtension,
                          mixed_vendor.driver_vendor_passthru,
                          'context',
                          'fake_method',
                          param='p1')

    def test_driver_passthru_mixin_unspecified(self):
        mixed_vendor = driver_utils.MixinVendorInterface({})
        self.assertRaises(exception.UnsupportedDriverExtension,
                          mixed_vendor.driver_vendor_passthru,
                          'context',
                          'fake_method',
                          param='p1')

    def test_get_node_mac_addresses(self):
        ports = []
        ports.append(
            self.dbapi.create_port(
                db_utils.get_test_port(
                    id=6,
                    address='aa:bb:cc',
                    uuid='bb43dc0b-03f2-4d2e-ae87-c02d7f33cc53',
                    node_id='123')))
        ports.append(
            self.dbapi.create_port(
                db_utils.get_test_port(
                    id=7,
                    address='dd:ee:ff',
                    uuid='4fc26c0b-03f2-4d2e-ae87-c02d7f33c234',
                    node_id='123')))
        with task_manager.acquire(self.context, [self.node.uuid]) as task:
            node_macs = driver_utils.get_node_mac_addresses(task, self.node)
        self.assertEqual(sorted([p.address for p in ports]), sorted(node_macs))
