# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from oslo_config import cfg
from oslo_log import log as logging

from ironic.drivers.modules.inspector.hooks import base

LOG = logging.getLogger(__name__)
CONF = cfg.CONF


class CPUSHook(base.InspectionHook):
    """Hook to set node's CPUs count based on the inventory."""

    def __call__(self, task, inventory, plugin_data):
        cpu = inventory.get('cpu', {})
        # NOTE(cardoe): count is deprecated in favor of thread_count but
        #               is still the only field reported by older
        #               inspection interfaces and ramdisks.
        cpu_count = int(cpu.get('thread_count') or cpu.get('count') or 0)
        if cpu_count == 0:
            LOG.warning('No CPU count available for node %s.', task.node.uuid)
            return

        task.node.set_property('cpus', cpu_count)
        task.node.save()
