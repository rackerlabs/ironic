# coding=utf-8

# Copyright 2013 Hewlett-Packard Development Company, L.P.
# Copyright 2013 International Business Machines Corporation
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
"""Conduct all activity related to bare-metal deployments.

A single instance of :py:class:`ironic.conductor.manager.ConductorManager` is
created within the *ironic-conductor* process, and is responsible for
performing all actions on bare metal resources (Chassis, Nodes, and Ports).
Commands are received via RPC calls. The conductor service also performs
periodic tasks, eg.  to monitor the status of active deployments.

Drivers are loaded via entrypoints by the
:py:class:`ironic.common.driver_factory` class. Each driver is instantiated
only once, when the ConductorManager service starts.  In this way, a single
ConductorManager may use multiple drivers, and manage heterogeneous hardware.

When multiple :py:class:`ConductorManager` are run on different hosts, they are
all active and cooperatively manage all nodes in the deployment.  Nodes are
locked by each conductor when performing actions which change the state of that
node; these locks are represented by the
:py:class:`ironic.conductor.task_manager.TaskManager` class.

A :py:class:`ironic.common.hash_ring.HashRing` is used to distribute nodes
across the set of active conductors which support each node's driver.
Rebalancing this ring can trigger various actions by each conductor, such as
building or tearing down the TFTP environment for a node, notifying Neutron of
a change, etc.
"""

import collections
import datetime

from eventlet import greenpool

from oslo.config import cfg

from ironic.common import driver_factory
from ironic.common import exception
from ironic.common import hash_ring as hash
from ironic.common import neutron
from ironic.common import service
from ironic.common import states
from ironic.conductor import task_manager
from ironic.conductor import utils
from ironic.db import api as dbapi
from ironic.objects import base as objects_base
from ironic.openstack.common import excutils
from ironic.openstack.common import lockutils
from ironic.openstack.common import log
from ironic.openstack.common import periodic_task
from ironic.openstack.common.rpc import common as messaging
from ironic.openstack.common import timeutils

MANAGER_TOPIC = 'ironic.conductor_manager'
WORKER_SPAWN_lOCK = "conductor_worker_spawn"

LOG = log.getLogger(__name__)

conductor_opts = [
        cfg.StrOpt('api_url',
                   default=None,
                   help=('URL of Ironic API service. If not set ironic can '
                         'get the current value from the keystone service '
                         'catalog.')),
        cfg.IntOpt('heartbeat_interval',
                   default=10,
                   help='Seconds between conductor heart beats.'),
        cfg.IntOpt('heartbeat_timeout',
                   default=60,
                   help='Maximum time (in seconds) since the last check-in '
                        'of a conductor.'),
        cfg.IntOpt('sync_power_state_interval',
                   default=60,
                   help='Interval between syncing the node power state to the '
                        'database, in seconds.'),
        cfg.IntOpt('check_provision_state_interval',
                   default=60,
                   help='Interval between checks of provision timeouts, '
                        'in seconds.'),
        cfg.IntOpt('deploy_callback_timeout',
                   default=1800,
                   help='Timeout (seconds) for waiting callback from deploy '
                        'ramdisk. 0 - unlimited.'),
        cfg.BoolOpt('force_power_state_during_sync',
                   default=True,
                   help='During sync_power_state, should the hardware power '
                        'state be set to the state recorded in the database '
                        '(True) or should the database be updated based on '
                        'the hardware state (False).'),
        cfg.IntOpt('power_state_sync_max_retries',
                   default=3,
                   help='During sync_power_state failures, limit the '
                        'number of times Ironic should try syncing the '
                        'hardware node power state with the node power state '
                        'in DB')
]

CONF = cfg.CONF
CONF.register_opts(conductor_opts, 'conductor')


class ConductorManager(service.PeriodicService):
    """Ironic Conductor service main class."""

    RPC_API_VERSION = '1.13'

    def __init__(self, host, topic):
        serializer = objects_base.IronicObjectSerializer()
        super(ConductorManager, self).__init__(host, topic,
                                               serializer=serializer)

        self.power_state_sync_count = collections.defaultdict(int)

    def start(self):
        super(ConductorManager, self).start()
        self.dbapi = dbapi.get_instance()

        # create a DriverFactory instance, which initializes the stevedore
        # extension manager, when the service starts.
        # TODO(deva): Enable re-loading of the DriverFactory to load new
        #             extensions without restarting the whole service.
        self.driver_factory = driver_factory.DriverFactory()
        self.drivers = self.driver_factory.names
        """List of driver names which this conductor supports."""

        try:
            self.dbapi.register_conductor({'hostname': self.host,
                                           'drivers': self.drivers})
        except exception.ConductorAlreadyRegistered:
            LOG.warn(_("A conductor with hostname %(hostname)s "
                       "was previously registered. Updating registration")
                       % {'hostname': self.host})
            self.dbapi.unregister_conductor(self.host)
            self.dbapi.register_conductor({'hostname': self.host,
                                           'drivers': self.drivers})

        self.driver_rings = self._get_current_driver_rings()
        """Consistent hash ring which maps drivers to conductors."""

        self._worker_pool = greenpool.GreenPool(size=CONF.rpc_thread_pool_size)
        """GreenPool of background workers for performing tasks async."""

    def stop(self):
        self.dbapi.unregister_conductor(self.host)
        super(ConductorManager, self).stop()

    def initialize_service_hook(self, service):
        pass

    def process_notification(self, notification):
        LOG.debug(_('Received notification: %r') %
                        notification.get('event_type'))
        # TODO(deva)

    def periodic_tasks(self, context, raise_on_error=False):
        """Periodic tasks are run at pre-specified interval."""
        return self.run_periodic_tasks(context, raise_on_error=raise_on_error)

    @messaging.client_exceptions(exception.InvalidParameterValue,
                                 exception.NodeLocked,
                                 exception.NodeInWrongPowerState)
    def update_node(self, context, node_obj):
        """Update a node with the supplied data.

        This method is the main "hub" for PUT and PATCH requests in the API.
        It ensures that the requested change is safe to perform,
        validates the parameters with the node's driver, if necessary.

        :param context: an admin context
        :param node_obj: a changed (but not saved) node object.

        """
        node_id = node_obj.uuid
        LOG.debug(_("RPC update_node called for node %s.") % node_id)

        delta = node_obj.obj_what_changed()
        if 'power_state' in delta:
            raise exception.IronicException(_(
                "Invalid method call: update_node can not change node state."))

        driver_name = node_obj.get('driver') if 'driver' in delta else None
        with task_manager.acquire(context, node_id, shared=False,
                                  driver_name=driver_name) as task:

            # TODO(deva): Determine what value will be passed by API when
            #             instance_uuid needs to be unset, and handle it.
            if 'instance_uuid' in delta:
                task.driver.power.validate(task, node_obj)
                node_obj['power_state'] = \
                        task.driver.power.get_power_state(task, node_obj)

                if node_obj['power_state'] != states.POWER_OFF:
                    raise exception.NodeInWrongPowerState(
                            node=node_id,
                            pstate=node_obj['power_state'])

            # update any remaining parameters, then save
            node_obj.save(context)

            return node_obj

    @messaging.client_exceptions(exception.NoFreeConductorWorker,
                                 exception.NodeLocked)
    def change_node_power_state(self, context, node_id, new_state):
        """RPC method to encapsulate changes to a node's state.

        Perform actions such as power on, power off. The validation and power
        action are performed in background (async). Once the power action is
        finished and successful, it updates the power_state for the node with
        the new power state.

        :param context: an admin context.
        :param node_id: the id or uuid of a node.
        :param new_state: the desired power state of the node.
        :raises: NoFreeConductorWorker when there is no free worker to start
                 async task.

        """
        LOG.debug(_("RPC change_node_power_state called for node %(node)s. "
                    "The desired new state is %(state)s.")
                    % {'node': node_id, 'state': new_state})

        task = task_manager.TaskManager(context, node_id, shared=False)

        try:
            # Start requested action in the background.
            thread = self._spawn_worker(utils.node_power_action,
                                        task, task.node, new_state)
            # Release node lock at the end.
            thread.link(lambda t: task.release_resources())
        except Exception:
            with excutils.save_and_reraise_exception():
                # Release node lock if error occurred.
                task.release_resources()

    @messaging.client_exceptions(exception.NoFreeConductorWorker,
                                 exception.NodeLocked,
                                 exception.InvalidParameterValue,
                                 exception.UnsupportedDriverExtension)
    def vendor_passthru(self, context, node_id, driver_method, info):
        """RPC method to encapsulate vendor action.

        Synchronously validate driver specific info or get driver status,
        and if successful, start background worker to perform vendor action
        asynchronously.

        :param context: an admin context.
        :param node_id: the id or uuid of a node.
        :param driver_method: the name of the vendor method.
        :param info: vendor method args.
        :raises: InvalidParameterValue if supplied info is not valid.
        :raises: UnsupportedDriverExtension if current driver does not have
                 vendor interface or method is unsupported.
        :raises: NoFreeConductorWorker when there is no free worker to start
                 async task.

        """
        LOG.debug(_("RPC vendor_passthru called for node %s.")
                    % node_id)
        # NOTE(max_lobur): Even though not all vendor_passthru calls may
        # require an exclusive lock, we need to do so to guarantee that the
        # state doesn't unexpectedly change between doing a vendor.validate
        # and vendor.vendor_passthru.
        task = task_manager.TaskManager(context, node_id, shared=False)

        try:
            if not getattr(task.driver, 'vendor', None):
                raise exception.UnsupportedDriverExtension(
                    driver=task.node.driver,
                    extension='vendor passthru')

            task.driver.vendor.validate(task, task.node, method=driver_method,
                                        **info)
            # Start requested action in the background.
            thread = self._spawn_worker(task.driver.vendor.vendor_passthru,
                                        task, task.node, method=driver_method,
                                        **info)
            # Release node lock at the end of async action.
            thread.link(lambda t: task.release_resources())
        except Exception:
            with excutils.save_and_reraise_exception():
                # Release node lock if error occurred.
                task.release_resources()

    @messaging.client_exceptions(exception.InvalidParameterValue,
                                 exception.UnsupportedDriverExtension)
    def driver_vendor_passthru(self, context, driver_name, driver_method,
                                  info):
        """RPC method which synchronously handles driver-level vendor passthru
        calls. These calls don't require a node UUID and are executed on a
        random conductor with the specified driver.

        :param context: an admin context.
        :param driver_name: name of the driver on which to call the method.
        :param driver_method: name of the vendor method, for use by the driver.
        :param info: data to pass through to the driver.
        :raises: InvalidParameterValue if supplied info is not valid.
        :raises: UnsupportedDriverExtension if current driver does not have
                 vendor interface, if the vendor interface does not impelement
                 driver-level vendor passthru or if the passthru method is
                 unsupported.

        """
        # Any locking in a top-level vendor action will need to be done by the
        # implementation, as there is little we could reasonably lock on here.
        LOG.debug(_("RPC driver_vendor_passthru for driver %s.")
                   % driver_name)
        try:
            driver = self.driver_factory[driver_name].obj
        except KeyError:
            raise exception.DriverNotFound(driver_name=driver_name)

        if not getattr(driver, 'vendor'):
            raise exception.UnsupportedDriverExtension(
                driver=driver_name,
                extension='vendor interface')

        return driver.vendor.driver_vendor_passthru(context,
                                                    method=driver_method,
                                                    **info)

    @messaging.client_exceptions(exception.NoFreeConductorWorker,
                                 exception.NodeLocked,
                                 exception.NodeInMaintenance,
                                 exception.InstanceDeployFailure)
    def do_node_deploy(self, context, node_id):
        """RPC method to initiate deployment to a node.

        Initiate the deployment of a node. Validations are done
        synchronously and the actual deploy work is performed in
        background (asynchronously).

        :param context: an admin context.
        :param node_id: the id or uuid of a node.
        :raises: InstanceDeployFailure
        :raises: NodeInMaintenance if the node is in maintenance mode.
        :raises: NoFreeConductorWorker when there is no free worker to start
                 async task.

        """
        LOG.debug(_("RPC do_node_deploy called for node %s.") % node_id)

        task = task_manager.TaskManager(context, node_id, shared=False)
        node = task.node
        try:
            if node.provision_state is not states.NOSTATE:
                raise exception.InstanceDeployFailure(_(
                    "RPC do_node_deploy called for %(node)s, but provision "
                    "state is already %(state)s.") %
                    {'node': node.uuid, 'state': node.provision_state})

            if node.maintenance:
                raise exception.NodeInMaintenance(op=_('provisioning'),
                                                  node=node.uuid)

            try:
                task.driver.deploy.validate(task, node)
            except exception.InvalidParameterValue as e:
                raise exception.InstanceDeployFailure(_(
                    "RPC do_node_deploy failed to validate deploy info. "
                    "Error: %(msg)s") % {'msg': e})

            # Set target state to expose that work is in progress
            node.provision_state = states.DEPLOYING
            node.target_provision_state = states.DEPLOYDONE
            node.last_error = None
            node.save(context)

            # Start requested action in the background.
            thread = self._spawn_worker(self._do_node_deploy, context, task)
            # Release node lock at the end.
            thread.link(lambda t: task.release_resources())
        except Exception:
            with excutils.save_and_reraise_exception():
                # Release node lock if error occurred.
                task.release_resources()

    def _do_node_deploy(self, context, task):
        """Prepare the environment and deploy a node."""
        node = task.node
        try:
            task.driver.deploy.prepare(task, node)
            new_state = task.driver.deploy.deploy(task, node)
        except Exception as e:
            with excutils.save_and_reraise_exception():
                node.last_error = _("Failed to deploy. Error: %s") % e
                node.provision_state = states.DEPLOYFAIL
                node.target_provision_state = states.NOSTATE
        else:
            # NOTE(deva): Some drivers may return states.DEPLOYWAIT
            #             eg. if they are waiting for a callback
            if new_state == states.DEPLOYDONE:
                node.target_provision_state = states.NOSTATE
                node.provision_state = states.ACTIVE
            else:
                node.provision_state = new_state
        finally:
            node.save(context)

    @messaging.client_exceptions(exception.NoFreeConductorWorker,
                                 exception.NodeLocked,
                                 exception.InstanceDeployFailure)
    def do_node_tear_down(self, context, node_id):
        """RPC method to tear down an existing node deployment.

        Validate driver specific information synchronously, and then
        spawn a background worker to tear down the node asynchronously.

        :param context: an admin context.
        :param node_id: the id or uuid of a node.
        :raises: InstanceDeployFailure
        :raises: NoFreeConductorWorker when there is no free worker to start
                 async task

        """
        LOG.debug(_("RPC do_node_tear_down called for node %s.") % node_id)

        task = task_manager.TaskManager(context, node_id, shared=False)
        node = task.node
        try:
            if node.provision_state not in [states.ACTIVE,
                                            states.DEPLOYFAIL,
                                            states.ERROR,
                                            states.DEPLOYWAIT]:
                raise exception.InstanceDeployFailure(_(
                    "RPC do_node_tear_down "
                    "not allowed for node %(node)s in state %(state)s")
                    % {'node': node_id, 'state': node.provision_state})

            try:
                task.driver.deploy.validate(task, node)
            except exception.InvalidParameterValue as e:
                raise exception.InstanceDeployFailure(_(
                    "RPC do_node_tear_down failed to validate deploy info. "
                    "Error: %(msg)s") % {'msg': e})

            node.provision_state = states.DELETING
            node.target_provision_state = states.DELETED
            node.last_error = None
            node.save(context)

            # Start requested action in the background.
            thread = self._spawn_worker(self._do_node_tear_down, context, task)

            # Release node lock at the end.
            thread.link(lambda t: task.release_resources())

        except Exception:
            with excutils.save_and_reraise_exception():
                # Release node lock if error occurred.
                task.release_resources()

    def _do_node_tear_down(self, context, task):
        """Internal RPC method to tear down an existing node deployment."""
        node = task.node
        try:
            task.driver.deploy.clean_up(task, node)
            new_state = task.driver.deploy.tear_down(task, node)
        except Exception as e:
            with excutils.save_and_reraise_exception():
                node.last_error = _("Failed to tear down. Error: %s") % e
                node.provision_state = states.ERROR
                node.target_provision_state = states.NOSTATE
        else:
            # NOTE(deva): Some drivers may return states.DELETING
            #             eg. if they are waiting for a callback
            if new_state == states.DELETED:
                node.target_provision_state = states.NOSTATE
                node.provision_state = states.NOSTATE
            else:
                node.provision_state = new_state
        finally:
            node.save(context)

    @periodic_task.periodic_task(spacing=CONF.conductor.heartbeat_interval)
    def _conductor_service_record_keepalive(self, context):
        self.dbapi.touch_conductor(self.host)

    def _handle_sync_power_state_max_retries_exceeded(self, task,
                                                      actual_power_state):
        node = task.node
        msg = (_("During sync_power_state, max retries exceeded "
                  "for node %(node)s, node state %(actual)s "
                  "does not match expected state '%(state)s'. "
                  "Updating DB state to '%(actual)s' "
                  "Switching node to maintenance mode.") %
                  {'node': node.uuid, 'actual': actual_power_state,
                   'state': node.power_state})
        node.power_state = actual_power_state
        node.last_error = msg
        node.maintenance = True
        node.save(task.context)
        LOG.error(msg)

    def _do_sync_power_state(self, task):
        node = task.node
        power_state = None

        try:
            power_state = task.driver.power.get_power_state(task, node)
        except Exception as e:
            # TODO(rloo): change to IronicException, after
            #             https://bugs.launchpad.net/ironic/+bug/1267693
            LOG.warning(_("During sync_power_state, could not get power "
                          "state for node %(node)s. Error: %(err)s."),
                          {'node': node.uuid, 'err': e})
            self.power_state_sync_count[node.uuid] += 1

            if (self.power_state_sync_count[node.uuid] >=
                CONF.conductor.power_state_sync_max_retries):
                self._handle_sync_power_state_max_retries_exceeded(task,
                                                                   power_state)
            return

        if node.power_state is None:
            LOG.info(_("During sync_power_state, node %(node)s has no "
                       "previous known state. Recording current state "
                       "'%(state)s'."),
                       {'node': node.uuid, 'state': power_state})
            node.power_state = power_state
            node.save(task.context)

        if power_state == node.power_state:
            if node.uuid in self.power_state_sync_count:
                del self.power_state_sync_count[node.uuid]
            return

        if not CONF.conductor.force_power_state_during_sync:
            LOG.warning(_("During sync_power_state, node %(node)s state "
                          "does not match expected state '%(state)s'. "
                          "Updating recorded state to '%(actual)s'."),
                          {'node': node.uuid, 'actual': power_state,
                           'state': node.power_state})
            node.power_state = power_state
            node.save(task.context)
            return

        if (self.power_state_sync_count[node.uuid] >=
            CONF.conductor.power_state_sync_max_retries):
            self._handle_sync_power_state_max_retries_exceeded(task,
                                                               power_state)
            return

        # Force actual power_state of node equal to DB power_state of node
        LOG.warning(_("During sync_power_state, node %(node)s state "
                      "'%(actual)s' does not match expected state. "
                      "Changing hardware state to '%(state)s'."),
                      {'node': node.uuid, 'actual': power_state,
                       'state': node.power_state})
        try:
            # node_power_action will update the node record
            # so don't do that again here.
            utils.node_power_action(task, task.node,
                                    node.power_state)
        except Exception as e:
            # TODO(rloo): change to IronicException after
            # https://bugs.launchpad.net/ironic/+bug/1267693
            LOG.error(_("Failed to change power state of node %(node)s "
                        "to '%(state)s'."), {'node': node.uuid,
                                             'state': node.power_state})
            attempts_left = (CONF.conductor.power_state_sync_max_retries -
                             self.power_state_sync_count[node.uuid]) - 1
            LOG.warning(_("%(left)s attempts remaining to "
                          "sync_power_state for node %(node)s"),
                          {'left': attempts_left,
                           'node': node.uuid})
        finally:
            # Update power state sync count for current node
            self.power_state_sync_count[node.uuid] += 1

    @periodic_task.periodic_task(
            spacing=CONF.conductor.sync_power_state_interval)
    def _sync_power_states(self, context):
        filters = {'reserved': False, 'maintenance': False}
        columns = ['id', 'uuid', 'driver']
        node_list = self.dbapi.get_nodeinfo_list(columns=columns,
                                                 filters=filters)
        for (node_id, node_uuid, driver) in node_list:
            # only sync power states for nodes mapped to this conductor
            mapped_hosts = self.driver_rings[driver].get_hosts(node_uuid)
            if self.host not in mapped_hosts:
                continue

            try:
                # prevent nodes in DEPLOYWAIT state from locking
                node = self.dbapi.get_node(node_uuid)
                if node.provision_state == states.DEPLOYWAIT:
                    continue

                with task_manager.acquire(context, node_id) as task:
                    self._do_sync_power_state(task)

            except exception.NodeNotFound:
                LOG.info(_("During sync_power_state, node %(node)s was not "
                           "found and presumed deleted by another process.") %
                           {'node': node_uuid})
                continue
            except exception.NodeLocked:
                LOG.info(_("During sync_power_state, node %(node)s was "
                           "already locked by another process. Skip.") %
                           {'node': node_uuid})
                continue

    @periodic_task.periodic_task(
            spacing=CONF.conductor.check_provision_state_interval)
    def _check_deploy_timeouts(self, context):
        if not CONF.conductor.deploy_callback_timeout:
            return

        filters = {'reserved': False, 'maintenance': False}
        columns = ['uuid', 'driver', 'provision_state', 'provision_updated_at']
        node_list = self.dbapi.get_nodeinfo_list(columns=columns,
                                                 filters=filters)

        for (node_uuid, driver, state, update_time) in node_list:
            mapped_hosts = self.driver_rings[driver].get_hosts(node_uuid)
            if self.host not in mapped_hosts:
                continue

            if state == states.DEPLOYWAIT:
                limit = (timeutils.utcnow() - datetime.timedelta(
                         seconds=CONF.conductor.deploy_callback_timeout))
                if timeutils.normalize_time(update_time) <= limit:
                    try:
                        task = task_manager.TaskManager(context, node_uuid)
                    except (exception.NodeLocked, exception.NodeNotFound):
                        continue

                    node = task.node
                    node.provision_state = states.DEPLOYFAIL
                    node.target_provision_state = states.NOSTATE
                    msg = (_('Timeout reached when waiting callback for '
                             'node %s') % node_uuid)
                    node.last_error = msg
                    LOG.error(msg)
                    node.save(task.context)

                    try:
                        thread = self._spawn_worker(
                                            utils.cleanup_after_timeout, task)
                        thread.link(lambda t: task.release_resources())
                    except exception.NoFreeConductorWorker:
                        task.release_resources()

    def _get_current_driver_rings(self):
        """Build the current hash ring for this ConductorManager's drivers."""

        ring = {}
        d2c = self.dbapi.get_active_driver_dict()

        for driver in self.drivers:
            ring[driver] = hash.HashRing(d2c[driver])
        return ring

    def rebalance_node_ring(self):
        """Perform any actions necessary when rebalancing the consistent hash.

        This may trigger several actions, such as calling driver.deploy.prepare
        for nodes which are now mapped to this conductor.

        """
        # TODO(deva): implement this
        pass

    @messaging.client_exceptions(exception.NodeLocked)
    def validate_driver_interfaces(self, context, node_id):
        """Validate the `core` and `standardized` interfaces for drivers.

        :param context: request context.
        :param node_id: node id or uuid.
        :returns: a dictionary containing the results of each
                  interface validation.

        """
        LOG.debug(_('RPC validate_driver_interfaces called for node %s.') %
                    node_id)
        ret_dict = {}
        with task_manager.acquire(context, node_id, shared=True) as task:
            for iface_name in (task.driver.core_interfaces +
                               task.driver.standard_interfaces):
                iface = getattr(task.driver, iface_name, None)
                result = reason = None
                if iface:
                    try:
                        iface.validate(task, task.node)
                        result = True
                    except (exception.InvalidParameterValue,
                            exception.UnsupportedDriverExtension) as e:
                        result = False
                        reason = str(e)
                else:
                    reason = _('not supported')

                ret_dict[iface_name] = {}
                ret_dict[iface_name]['result'] = result
                if reason is not None:
                    ret_dict[iface_name]['reason'] = reason
        return ret_dict

    @messaging.client_exceptions(exception.NodeLocked,
                                 exception.NodeMaintenanceFailure)
    def change_node_maintenance_mode(self, context, node_id, mode):
        """Set node maintenance mode on or off.

        :param context: request context.
        :param node_id: node id or uuid.
        :param mode: True or False.
        :raises: NodeMaintenanceFailure

        """
        LOG.debug(_("RPC change_node_maintenance_mode called for node %(node)s"
                    " with maintenance mode: %(mode)s") % {'node': node_id,
                                                           'mode': mode})

        with task_manager.acquire(context, node_id, shared=True) as task:
            node = task.node
            if mode is not node.maintenance:
                node.maintenance = mode
                node.save(context)
            else:
                msg = _("The node is already in maintenance mode") if mode \
                        else _("The node is not in maintenance mode")
                raise exception.NodeMaintenanceFailure(node=node_id,
                                                       reason=msg)

            return node

    @lockutils.synchronized(WORKER_SPAWN_lOCK, 'ironic-')
    def _spawn_worker(self, func, *args, **kwargs):

        """Create a greenthread to run func(*args, **kwargs).

        Spawns a greenthread if there are free slots in pool, otherwise raises
        exception. Execution control returns immediately to the caller.

        :returns: GreenThread object.
        :raises: NoFreeConductorWorker if worker pool is currently full.

        """
        if self._worker_pool.free():
            return self._worker_pool.spawn(func, *args, **kwargs)
        else:
            raise exception.NoFreeConductorWorker()

    @messaging.client_exceptions(exception.NodeLocked,
                                 exception.NodeAssociated,
                                 exception.NodeInWrongPowerState)
    def destroy_node(self, context, node_id):
        """Delete a node.

        :param context: request context.
        :param node_id: node id or uuid.
        :raises: NodeLocked if node is locked by another conductor.
        :raises: NodeAssociated if the node contains an instance
            associated with it.
        :raises: NodeInWrongPowerState if the node is not powered off.

        """
        with task_manager.acquire(context, node_id) as task:
            node = task.node
            if node.instance_uuid is not None:
                raise exception.NodeAssociated(node=node.uuid,
                                               instance=node.instance_uuid)
            if node.power_state not in [states.POWER_OFF, states.NOSTATE]:
                msg = (_("Node %s can't be deleted because it's not "
                         "powered off") % node.uuid)
                raise exception.NodeInWrongPowerState(msg)

            self.dbapi.destroy_node(node_id)

    @messaging.client_exceptions(exception.NodeLocked,
                                 exception.UnsupportedDriverExtension,
                                 exception.NodeConsoleNotEnabled,
                                 exception.InvalidParameterValue)
    def get_console_information(self, context, node_id):
        """Get connection information about the console.

        :param context: request context.
        :param node_id: node id or uuid.
        :raises: UnsupportedDriverExtension if the node's driver doesn't
                 support console.
        :raises: NodeConsoleNotEnabled if the console is not enabled.
        :raises: InvalidParameterValue when the wrong driver info is specified.
        """
        LOG.debug(_('RPC get_console_information called for node %s')
                  % node_id)

        with task_manager.acquire(context, node_id, shared=True) as task:
            node = task.node

            if not getattr(task.driver, 'console', None):
                raise exception.UnsupportedDriverExtension(driver=node.driver,
                                                           extension='console')
            if not node.console_enabled:
                raise exception.NodeConsoleNotEnabled(node=node_id)

            task.driver.console.validate(task, node)
            return task.driver.console.get_console(task, node)

    @messaging.client_exceptions(exception.NoFreeConductorWorker,
                                 exception.NodeLocked,
                                 exception.UnsupportedDriverExtension,
                                 exception.InvalidParameterValue)
    def set_console_mode(self, context, node_id, enabled):
        """Enable/Disable the console.

        Validate driver specific information synchronously, and then
        spawn a background worker to set console mode asynchronously.

        :param context: request context.
        :param node_id: node id or uuid.
        :param enabled: Boolean value; whether the console is enabled or
                        disabled.
        :raises: UnsupportedDriverExtension if the node's driver doesn't
                 support console.
        :raises: InvalidParameterValue when the wrong driver info is specified.
        :raises: NoFreeConductorWorker when there is no free worker to start
                 async task
        """
        LOG.debug(_('RPC set_console_mode called for node %(node)s with '
                    'enabled %(enabled)s') % {'node': node_id,
                                              'enabled': enabled})

        task = task_manager.TaskManager(context, node_id, shared=False)
        node = task.node

        try:
            if not getattr(task.driver, 'console', None):
                exc = exception.UnsupportedDriverExtension(driver=node.driver,
                                                           extension='console')
                node.last_error = exc.format_message()
                node.save(context)
                raise exc

            task.driver.console.validate(task, node)

            if enabled == node.console_enabled:
                op = _('enabled') if enabled else _('disabled')
                LOG.info(_("No console action was triggered because the "
                           "console is already %s") % op)
            else:
                node.last_error = None
                node.save(context)

                # Start the requested action in the background.
                thread = self._spawn_worker(self._set_console_mode,
                                            task, enabled)
                # Release node lock at the end.
                thread.link(lambda t: task.release_resources())

        except Exception:
            with excutils.save_and_reraise_exception():
                # Release node lock if error occurred.
                task.release_resources()

    def _set_console_mode(self, task, enabled):
        """Internal method to set console mode on a node."""
        node = task.node
        try:
            if enabled:
                task.driver.console.start_console(task, node)
            else:
                task.driver.console.stop_console(task, node)
        except Exception as e:
            with excutils.save_and_reraise_exception():
                op = _('enabling') if enabled else _('disabling')
                msg = (_('Error %(op)s the console on node %(node)s. '
                        'Reason: %(error)s') % {'op': op,
                                                'node': node.uuid,
                                                'error': e})
                node.last_error = msg
        else:
            node.console_enabled = enabled
            node.last_error = None
        finally:
            node.save(task.context)

    @messaging.client_exceptions(exception.NodeLocked,
                                 exception.FailedToUpdateMacOnPort)
    def update_port(self, context, port_obj):
        """Update a port.

        :param context: request context.
        :param port_obj: a changed (but not saved) port object.
        :raises: FailedToUpdateMacOnPort if MAC address changed and update
                 Neutron failed.
        """
        port_uuid = port_obj.uuid
        LOG.debug(_("RPC update_port called for port %s."), port_uuid)

        with task_manager.acquire(context, port_obj.node_id) as task:
            node = task.node
            if 'address' in port_obj.obj_what_changed():
                vif = port_obj.extra.get('vif_port_id')
                if vif:
                    api = neutron.NeutronAPI(context)
                    api.update_port_address(vif, port_obj.address)
                # Log warning if there is no vif_port_id and an instance
                # is associated with the node.
                elif node.instance_uuid:
                    LOG.warning(_("No VIF found for instance %(instance)s "
                        "port %(port)s when attempting to update Neutron "
                        "port MAC address."),
                        {'port': port_uuid, 'instance': node.instance_uuid})

            port_obj.save(context)

            return port_obj
