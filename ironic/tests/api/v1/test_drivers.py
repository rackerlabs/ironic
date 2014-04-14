# Copyright 2013 Red Hat, Inc.
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
from testtools.matchers import HasLength

from ironic.conductor import rpcapi
from ironic.tests.api import base


class TestListDrivers(base.FunctionalTest):
    d1 = 'fake-driver1'
    d2 = 'fake-driver2'
    h1 = 'fake-host1'
    h2 = 'fake-host2'

    def register_fake_conductors(self):
        self.dbapi.register_conductor({
            'hostname': self.h1,
            'drivers': [self.d1, self.d2],
        })
        self.dbapi.register_conductor({
            'hostname': self.h2,
            'drivers': [self.d2],
        })

    def test_drivers(self):
        self.register_fake_conductors()
        expected = sorted([
            {'name': self.d1, 'hosts': [self.h1]},
            {'name': self.d2, 'hosts': [self.h1, self.h2]},
        ])
        data = self.get_json('/drivers')
        self.assertThat(data['drivers'], HasLength(2))
        drivers = sorted(data['drivers'])
        for i in range(len(expected)):
            driver = drivers[i]
            self.assertEqual(expected[i]['name'], driver['name'])
            self.assertEqual(expected[i]['hosts'], driver['hosts'])
            self.validate_link(driver['links'][0]['href'])
            self.validate_link(driver['links'][1]['href'])

    def test_drivers_no_active_conductor(self):
        data = self.get_json('/drivers')
        self.assertThat(data['drivers'], HasLength(0))
        self.assertEqual([], data['drivers'])

    def test_drivers_get_one_ok(self):
        self.register_fake_conductors()
        data = self.get_json('/drivers/%s' % self.d1)
        self.assertEqual(self.d1, data['name'])
        self.assertEqual([self.h1], data['hosts'])
        self.validate_link(data['links'][0]['href'])
        self.validate_link(data['links'][1]['href'])

    def test_drivers_get_one_not_found(self):
        response = self.get_json('/drivers/%s' % self.d1, expect_errors=True)
        self.assertEqual(404, response.status_int)

    @mock.patch.object(rpcapi.ConductorAPI, 'driver_vendor_passthru')
    def test_driver_vendor_passthru_ok(self, mocked_driver_vendor_passthru):
        self.register_fake_conductors()
        mocked_driver_vendor_passthru.return_value = {
            'return_key': 'return_value',
        }
        response = self.post_json(
            '/drivers/%s/vendor_passthru/do_test' % self.d1,
            {'test_key': 'test_value'})
        self.assertEqual(200, response.status_int)
        self.assertEqual(mocked_driver_vendor_passthru.return_value,
                         response.json)

    def test_driver_vendor_passthru_not_found(self):
        response = self.post_json(
            '/drivers/%s/vendor_passthru/do_test' % self.d1,
            {'test_key': 'test_value'},
            expect_errors=True)

        self.assertEqual(404, response.status_int)
