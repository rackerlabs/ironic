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

from ironic.drivers import base
from ironic.drivers.modules import agent
from ironic.drivers.modules import ipminative
from ironic.drivers.modules import ipmitool
from ironic.drivers.modules import ssh
from ironic.drivers import utils


class AgentAndIPMIToolDriver(base.BaseDriver):
    """Agent + IPMITool driver.

    This driver implements the `core` functionality, combining
    :class:`ironic.drivers.ipmitool.IPMIPower` for power on/off and reboot with
    :class:`ironic.driver.agent_deploy.AgentDeploy` for image deployment.
    Implementations are in those respective classes; this class is merely the
    glue between them.
    """

    def __init__(self):
        self.power = ipmitool.IPMIPower()
        self.deploy = agent.AgentDeploy()
        self.agent_vendor = agent.AgentVendorInterface()
        self.ipmi_vendor = ipmitool.VendorPassthru()
        #TODO(JoshNang) add lookup: nodeless passthru mapping when its added
        #to utils.MixinVendorInterface
        self.mapping = {'heartbeat': self.agent_vendor,
                        'set_boot_device': self.ipmi_vendor}
        self.vendor = utils.MixinVendorInterface(self.mapping)


class AgentAndIPMINativeDriver(base.BaseDriver):
    """Agent + IPMINative driver.

    This driver implements the `core` functionality, combining
    :class:`ironic.drivers.ipminative.NativeIPMIPower` for power on/off and
    reboot with
    :class:`ironic.driver.agent_deploy.AgentDeploy` for image deployment.
    Implementations are in those respective classes; this class is merely the
    glue between them.
    """

    def __init__(self):
        self.power = ipminative.NativeIPMIPower()
        self.deploy = agent.AgentDeploy()
        self.agent_vendor = agent.AgentVendorInterface()
        self.ipmi_vendor = ipminative.VendorPassthru()
        #TODO(JoshNang) add lookup: nodeless passthru mapping when its added
        #to utils.MixinVendorInterface
        self.mapping = {'heartbeat': self.agent_vendor,
                        'set_boot_device': self.ipmi_vendor}
        self.vendor = utils.MixinVendorInterface(self.mapping)


class AgentAndSSHDriver(base.BaseDriver):
    """Agent + SSH driver.

    NOTE: This driver is meant only for testing environments.

    This driver implements the `core` functionality, combining
    :class:`ironic.drivers.ssh.SSH` for power on/off and reboot of virtual
    machines tunneled over SSH, with :class:`ironic.driver.modules
    .agent_driver.AgentDeploy` for image deployment. Implementations are in
    those respective classes; this class is merely the glue between them.
    """

    def __init__(self):
        self.power = ssh.SSHPower()
        self.deploy = agent.AgentDeploy()
        self.vendor = agent.AgentVendorInterface()
        self.mapping = {'heartbeat': self.agent_vendor}
