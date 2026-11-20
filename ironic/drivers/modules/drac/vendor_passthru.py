#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

"""
DRAC vendor-passthru interface
"""

import re

import sushy

from ironic.common import exception
from ironic.common.i18n import _
from ironic.drivers import base
from ironic.drivers.modules.drac import support_assist
from ironic.drivers.modules.redfish import utils as redfish_utils
from ironic.drivers.modules.redfish import vendor as redfish_vendor


class DracRedfishVendorPassthru(redfish_vendor.RedfishVendorPassthru):
    """Dell actions and task status alongside standard Redfish passthru."""

    def validate(self, task, method, **kwargs):
        """Validate Dell inputs, preserving inherited methods."""
        if method == 'get_job_status':
            unknown = set(kwargs) - {'task_monitor', 'http_method'}
            if unknown:
                raise exception.InvalidParameterValue(
                    _('Unknown get_job_status arguments: %s')
                    % sorted(unknown))
            uri = kwargs.get('task_monitor')
            if (not isinstance(uri, str) or not re.fullmatch(
                    r'/redfish/v1/TaskService/(?:TaskMonitors|Tasks)/'
                    r'[A-Za-z0-9_-]+', uri)):
                raise exception.InvalidParameterValue(
                    _('task_monitor must be a node-relative Redfish '
                      'TaskService Tasks or TaskMonitors URI returned by '
                      'an asynchronous operation'))
            return
        if method in support_assist.METHOD_ARGUMENTS:
            return support_assist.validate(method, **kwargs)
        return super().validate(task, method, **kwargs)

    @base.passthru(['GET'], async_call=False, require_exclusive_lock=False,
                   description=_('Get SupportAssist EULA acceptance status.'))
    def support_assist_get_eula_status(self, task, **kwargs):
        """Return EULA status and the original Dell status messages."""
        return support_assist.get_eula_status(task, **kwargs)

    @base.passthru(['POST'], async_call=False,
                   description=_('Explicitly accept the SupportAssist EULA.'))
    def support_assist_accept_eula(self, task, **kwargs):
        """Accept EULA only at the operator's explicit request."""
        return support_assist.accept_eula(task, **kwargs)

    @base.passthru(['POST'], async_call=False,
                   description=_('Register SupportAssist using registration '
                                 'contact fields, if supported by firmware.'))
    def support_assist_register(self, task, **kwargs):
        """Register without changing OS-to-BMC settings or accepting EULA."""
        return support_assist.register(task, **kwargs)

    @base.passthru(['POST'], async_call=False,
                   description=_('Collect SupportAssist to the configured '
                                 'share. Optional: data_types, filter_data.'))
    def support_assist_collect(self, task, **kwargs):
        """Start collection and return a handle without waiting."""
        return support_assist.collect(task, **kwargs)

    @base.passthru(['GET'], async_call=False, require_exclusive_lock=False,
                   description=_('Get Redfish job status using its '
                                 'task_monitor.'))
    def get_job_status(self, task, **kwargs):
        """Read one task response without following a result Location."""
        try:
            monitor = redfish_utils.get_task_monitor(
                task.node, kwargs['task_monitor'])
            response = monitor.response
            data = response.json() if response.content else {}
        except sushy.exceptions.SushyError as exc:
            raise exception.RedfishError(error=str(exc))
        dell = data.get('Oem', {}).get('Dell', {})
        state = data.get('TaskState') or dell.get('JobState')
        if not state:
            state = 'Running' if monitor.is_processing else 'Completed'
        terminal = {'Completed', 'Exception', 'Killed', 'Cancelled',
                    'Interrupted', 'Failed', 'CompletedWithErrors'}
        return {
            'task_monitor': monitor.task_monitor_uri,
            'is_processing': state not in terminal,
            'task_state': state,
            'task_status': data.get('TaskStatus'),
            'percent_complete': data.get('PercentComplete'),
            'messages': data.get('Messages',
                                 data.get('@Message.ExtendedInfo', [])),
            'job_state': dell.get('JobState'),
        }
