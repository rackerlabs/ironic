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

"""Regression tests for standalone out-of-band step failures."""

from unittest import mock

from ironic.common import exception
from ironic.common import states
from ironic.conductor import cleaning
from ironic.conductor import servicing
from ironic.conductor import task_manager
from ironic.conductor import utils
from ironic.drivers import base
from ironic.drivers.modules.drac import management
from ironic.tests.unit.db import base as db_base
from ironic.tests.unit.objects import utils as obj_utils


class PreserveStepFailureTestCase(db_base.DbTestCase):
    def _check_failure(
        self,
        kind,
        opt_in=True,
        disable_ramdisk=True,
        compound=False,
        automated=False,
    ):
        service = kind == "service"
        initial = states.SERVICING if service else states.CLEANING
        expected = states.ACTIVE if service else states.MANAGEABLE
        if automated:
            expected = states.AVAILABLE
        self.config(poweroff_in_servicefail=True, group="conductor")
        self.config(poweroff_in_cleanfail=True, group="conductor")
        step = {
            "interface": "management",
            "step": "configure_snmpv3_user",
            "priority": 0,
        }
        # Caller-supplied flags cannot opt an unmarked driver method in.
        step["preserve_state_on_failure"] = True
        plan = [step, step.copy()] if compound else [step]
        node = obj_utils.create_test_node(
            self.context,
            driver="fake-hardware",
            provision_state=initial,
            target_provision_state=expected,
            power_state=states.POWER_ON,
            driver_internal_info={
                kind + "_steps": plan,
                kind + "_step_index": None,
            },
        )
        with task_manager.acquire(self.context, node.uuid) as task:
            method = management.DracRedfishManagement.configure_snmpv3_user
            if not opt_in:

                def method(task):
                    pass

            task.driver.management.configure_snmpv3_user = method
            execute = mock.Mock(
                side_effect=exception.RedfishError(error="SNMP update failed")
            )
            setattr(
                task.driver.management, "execute_" + kind + "_step", execute
            )
            teardown = mock.Mock()
            setattr(
                task.driver.deploy,
                "tear_down_" + ("service" if service else "cleaning"),
                teardown,
            )
            with mock.patch.object(
                utils, "node_power_action", autospec=True
            ) as power:
                if service:
                    servicing.do_next_service_step(
                        task, 0, disable_ramdisk=disable_ramdisk
                    )
                else:
                    cleaning.do_next_clean_step(
                        task, 0, disable_ramdisk=disable_ramdisk
                    )
                preserved = (
                    opt_in and disable_ramdisk and not (compound or automated)
                )
                if preserved:
                    teardown.assert_not_called()
                    power.assert_not_called()
                else:
                    teardown.assert_called_once_with(task)
        node.refresh()
        self.assertIn("SNMP update failed", node.last_error)
        self.assertEqual(states.POWER_ON, node.power_state)
        if preserved:
            self.assertEqual(expected, node.provision_state)
            self.assertIsNone(node.target_provision_state)
            self.assertFalse(node.maintenance)
            self.assertIsNone(node.fault)
        else:
            self.assertEqual(
                states.SERVICEFAIL if service else states.CLEANFAIL,
                node.provision_state,
            )
            self.assertTrue(node.maintenance)
        self.assertFalse(node.service_step if service else node.clean_step)
        self.assertNotIn(kind + "_step_index", node.driver_internal_info)

    def test_manual_snmp_clean_failure_preserves_state_and_power(self):
        self._check_failure("clean")

    def test_snmp_service_failure_preserves_state_and_power(self):
        self._check_failure("service")

    def test_unmarked_clean_step_retains_failure_behavior(self):
        self._check_failure("clean", opt_in=False)

    def test_unmarked_service_step_retains_failure_behavior(self):
        self._check_failure("service", opt_in=False)

    def test_inband_clean_retains_failure_behavior(self):
        self._check_failure("clean", disable_ramdisk=False)

    def test_inband_service_retains_failure_behavior(self):
        self._check_failure("service", disable_ramdisk=False)

    def test_compound_clean_retains_failure_behavior(self):
        self._check_failure("clean", compound=True)

    def test_compound_service_retains_failure_behavior(self):
        self._check_failure("service", compound=True)

    def test_automated_clean_retains_failure_behavior(self):
        self._check_failure("clean", automated=True)

    def test_decorators_reject_state_preservation_with_ramdisk(self):
        for decorator in (base.clean_step, base.service_step):
            self.assertRaises(
                exception.InvalidParameterValue,
                decorator,
                priority=0,
                preserve_state_on_failure=True,
            )
