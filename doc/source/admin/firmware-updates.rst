Firmware Updates
================

The firmware update step allows one or more firmware updates to be applied
to a node via cleaning or servicing. If multiple updates are specified, then
they are applied
sequentially in the order given. The server is rebooted once per update.
If a failure occurs, the step immediately fails which may result
in some updates not being applied. If the node is placed into maintenance
mode while a firmware update step is running that is performing
multiple firmware updates, the update in progress will complete, and processing
of the remaining updates will pause.  When the node is taken out of maintenance
mode, processing of the remaining updates will continue.

.. note:: Only :doc:`/admin/drivers/redfish` supports firmware updates
   currently.

When updating the BMC firmware, the BMC may become unavailable for a period of
time as it resets. In this case, it may be desirable to have the step
wait after the update has been applied before indicating that the
update was successful. This allows the BMC time to fully reset before further
operations are carried out against it. To cause the step to wait after
applying an update, an optional ``wait`` argument may be specified in the
firmware image dictionary. The value of this argument indicates the number of
seconds to wait following the update. If the ``wait`` argument is not
specified, then this is equivalent to ``wait 0``, meaning that it will not
wait and immediately proceed with the next firmware update if there is one,
or complete the step if not.

Firmware inventory
------------------

The Redfish firmware interface caches BIOS, BMC, NIC and additional components
reported by ``UpdateService/FirmwareInventory``. List the cached inventory with::

    openstack baremetal node firmware list <node>

BIOS, BMC and NICs retain their existing component names. Additional components
use ``firmware:<device ID>``, taking the final segment of the related resource
URI, for example ``firmware:PSU.Slot.3``. Numeric IDs, IDs shared by multiple
resource paths, and IDs that collide with selectors from entries without
``RelatedItem`` retain the full URI to keep devices distinct. The full URI is
always retained for update targeting. Each device gets its own row.
For older Dell iDRAC systems without ``RelatedItem``, the device
suffix following ``__`` in the inventory ID identifies the device instead.
For example, a power supply uses ``firmware:PSU.Slot.1``.
Entries without device information use the software ID for Current or Installed
entries, or the inventory ID for generic entries. Identifiers exceeding
255 characters use a truncated prefix and a SHA-256 digest of the full identifier.
Previous and Available Dell entries are excluded. For duplicates, Current
entries take precedence over Installed entries, followed by generic IDs.

The inventory is refreshed during inspection, cleaning and after firmware
updates. Rows for devices that are later removed are not deleted.
Selectors do not include Redfish Name, which can contain firmware versions.
They depend on device identifiers reported by the BMC. Use the
exact component value from a refreshed inventory when constructing a runbook.
If a newly discovered resource or entry without ``RelatedItem`` collides with
an existing short selector, affected resources with ``RelatedItem`` use full-URI
selectors. The old short selector is rejected as ambiguous while the collision
exists; it does not select the entry without ``RelatedItem`` instead.
Previously cached component names are not renamed or deleted automatically.

Writing and running a firmware runbook
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Refresh the node's inventory as described in
`Refreshing inventory on active nodes`_, then list its components::

    openstack baremetal node firmware list <node>

Copy the intended device's ``component`` value into ``args.settings`` in your
runbook. For example, this step selects PSU slot 1:

.. code-block:: yaml

    - interface: firmware
      step: update
      order: 1
      args:
        settings:
          - component: firmware:PSU.Slot.1
            url: https://example.com/psu.exe

You can reuse this step on iDRAC9 and iDRAC10 servers when
``firmware:PSU.Slot.1`` identifies one PSU on each server. You do not need
separate selectors for the two generations. Check the intended device and
firmware image compatibility on every hardware type covered by the runbook.

A full-URI selector has the form ``firmware:<full Redfish URI>``. Use the
device's ``RelatedItem`` URI when present, or its FirmwareInventory entry URI
otherwise. The examples below show what to put in your runbook and what to
expect when you execute it. Each update checks live inventory, even if the
runbook was written using an older inventory listing.

1. **You want one runbook for iDRAC9 and iDRAC10 servers.**

   You want to update PSU slot 1 on both generations. On iDRAC9, the PSU is
   identified by an inventory ID ending in ``__PSU.Slot.1``. On iDRAC10, its
   related URI ends in ``/PSU.Slot.1``.

   Put ``firmware:PSU.Slot.1`` in the runbook. When you execute it on either
   server, it selects that PSU, provided the ID identifies one device there.
   You do not need a separate runbook just because the iDRAC generation differs.

   For example, these two servers use the same saved setting::

       Runbook component: firmware:PSU.Slot.1
       Run on iDRAC9:     selects PSU slot 1
       Run on iDRAC10:    selects PSU slot 1

   Before using it across a group of servers, check that each server has one
   matching PSU and that the image supports each server's hardware.

2. **You want to update only one of two PSUs.**

   Your server has two PSUs. Inventory lists them separately::

       firmware:PSU.Slot.1
       firmware:PSU.Slot.2

   To update only slot 1, write this under the update step's ``args``:

   .. code-block:: yaml

       settings:
         - component: firmware:PSU.Slot.1
           url: https://example.com/psu.exe

   When you execute the runbook, this setting targets slot 1. Slot 2 is not
   selected by this setting, even if both PSUs use the same firmware image.
   The short selector identifies a particular slot; it does not mean "all
   PSUs" or "any PSU that accepts this image".

   To update both slots, list both explicitly:

   .. code-block:: yaml

       settings:
         - component: firmware:PSU.Slot.1
           url: https://example.com/psu.exe
         - component: firmware:PSU.Slot.2
           url: https://example.com/psu.exe

   Execution processes the settings in order: slot 1, then slot 2. Use the
   same image URL only when it is appropriate for both PSUs. After execution,
   check the reported version for each slot you intended to update.

3. **Two chassis have a PSU with the same device ID.**

   You want the PSU in chassis 1, but both chassis report ``PSU.Slot.1``::

       /redfish/v1/Chassis/1/PowerSupplies/PSU.Slot.1
       /redfish/v1/Chassis/2/PowerSupplies/PSU.Slot.1

   Using ``firmware:PSU.Slot.1`` fails because it matches both PSUs. To select
   the first PSU, put this explicit selector in the runbook::

       firmware:/redfish/v1/Chassis/1/PowerSupplies/PSU.Slot.1

   Check that this URI identifies the intended PSU on every node where you
   plan to execute that runbook.

   Executing the corrected setting targets only chassis 1's PSU. To update
   chassis 2's PSU as well, add a separate setting with its full URI. Keeping
   the short selector and retrying cannot tell Ironic which chassis you mean.

4. **Two controllers share an ID but report it differently.**

   You want controller A, whose related URI is
   ``/redfish/v1/Systems/1/Controllers/RAID``.
   Controller B also identifies itself as ``RAID``, but has no ``RelatedItem``.

   A runbook using ``firmware:RAID`` fails as ambiguous. It does not select B
   just because B still appears under the short name in cached inventory.
   The error lists explicit selectors for both controllers. Identify A's
   related URI in that list and use its full selector for this update. To
   select B instead, use its FirmwareInventory entry URI from the error.

   For example, if B's entry URI is
   ``/redfish/v1/UpdateService/FirmwareInventory/RAID``, the choices are::

       A: firmware:/redfish/v1/Systems/1/Controllers/RAID
       B: firmware:/redfish/v1/UpdateService/FirmwareInventory/RAID

   Put the selector for the controller you intend to update in ``component``.
   Executing that setting selects that controller. You do not need to make
   both controllers report their identity in the same format.

5. **A collision appears after you wrote the runbook.**

   When you write the runbook, only controller A has device ID ``RAID``.
   You save ``firmware:RAID`` in ``args.settings`` and successfully update A.

   Later, another inventory entry, B, is discovered with the same ID. You
   have not edited the saved runbook: it still contains ``firmware:RAID``.

   On the next execution, that selector matches both A and B. The update is
   rejected as ambiguous before its image is staged or submitted. Ironic does
   not silently change the target from A to B, and rerunning the unchanged
   runbook while both entries remain will fail again.

   Read the explicit selectors in the error, identify A, and use A's full-URI
   selector in a runbook for the affected node or hardware group. If other
   nodes share the original runbook, check them before changing its selector.

   For example, if A's URI is
   ``/redfish/v1/Systems/1/Controllers/RAID``, the change is::

       Saved component:     firmware:RAID
       Corrected component: firmware:/redfish/v1/Systems/1/Controllers/RAID

   The corrected setting selects A even while B remains present. Use B's
   explicit selector instead only if you intend to update B.

6. **A conflicting device disappears after you saved a full URI.**

   A and B share ID ``RAID``, so you save A's full-URI selector in the runbook::

       firmware:/redfish/v1/Systems/1/Controllers/RAID

   Later, B disappears. A is now the only matching device, and refreshed
   inventory shows it as ``firmware:RAID``.

   Execute the saved runbook without changing its full URI. It still selects A
   while A remains installed and updateable at that URI. You do not need to
   replace the saved selector just because the displayed name became shorter.
   A runbook using the short selector would also work again while A is the
   only match.

   The displayed component name changed; your intended controller and its
   URI did not. Keep the existing full selector and verify A's reported
   version after the run.

7. **You prefer a full URI even when inventory shows a short name.**

   Inventory shows ``firmware:PSU.Slot.1`` for a PSU whose related URI is
   ``/redfish/v1/Chassis/1/PowerSupplies/PSU.Slot.1``. You can put either of
   these selectors in the runbook::

       firmware:PSU.Slot.1
       firmware:/redfish/v1/Chassis/1/PowerSupplies/PSU.Slot.1

   Choose one of these values for ``component``; you do not need settings for
   both. Either setting selects the same PSU when executed on that node.

   Keeping the short selector lets you share the runbook with servers that
   use another URI for the same unique slot ID. Keeping the full URI names
   that particular resource explicitly. Before sharing the full-URI version,
   check that URI on the other target nodes. You do not need to wait for a
   naming collision before using a full URI.

8. **Current and Installed entries describe the same PSU.**

   You see inventory entries such as ``Current-123-2.0__PSU.Slot.1`` and
   ``Installed-123-1.0__PSU.Slot.1``. These represent the same PSU, not two
   different slots. Keep ``firmware:PSU.Slot.1`` in your runbook; Current takes
   precedence over Installed when resolving this device.

   Write one setting for ``firmware:PSU.Slot.1``. Do not create two settings
   just because you see Current and Installed records for it. When executed,
   that setting resolves to the Current record and targets the PSU once.

   This preference does not choose between different devices. If the selector
   also matches another related URI, or matches both a related resource and an
   entry without ``RelatedItem``, execution fails as ambiguous.

   In that case, identify the intended device using the explicit selectors
   in the error and correct the setting before retrying.

9. **The selected device is missing or cannot be updated.**

   Your saved runbook contains ``firmware:PSU.Slot.1``. It used to select an
   installed PSU. Before the next run, one of these changes occurs:

   * The PSU is removed and no installed entry matches the selector.
   * The PSU is still present but Redfish marks it non-updateable.
   * Only Previous or Available firmware entries remain for that ID.

   In each case, executing that setting fails. Ironic does not select slot 2,
   select an old firmware record, or drop the selector and submit an
   untargeted update.

   Refresh inventory and check the device before editing or retrying the
   runbook. An old row in cached inventory does not prove that the device is
   still an installed, updateable target.

   If the device should be present, resolve its inventory or updateability
   problem before retrying. If your intended target has changed, identify
   the replacement device and change the runbook deliberately.

10. **Inventory uses a numeric resource ID.**

    The PSU's related URI is
    ``/redfish/v1/Chassis/1/PowerSupplies/1``. Copy its full component value
    into the runbook::

        firmware:/redfish/v1/Chassis/1/PowerSupplies/1

    Keep the full selector when executing the runbook. Do not shorten it to
    ``firmware:1``; the driver does not create that alias for the related
    resource.

    For example, chassis 2 could also have a PSU whose resource ID is ``1``::

        firmware:/redfish/v1/Chassis/2/PowerSupplies/1

    The chassis 1 selector targets chassis 1's PSU. To update chassis 2's PSU,
    use the second full selector in a separate setting. Before reusing either
    setting on another server, check which PSU that URI identifies there.

Firmware version and Redfish Name changes alone do not change device-ID
selectors. For duplicate inventory entries with different software IDs, the
driver rejects the selector as ambiguous even across Current/Installed levels.

A selector error stops that component's update; earlier steps or component
updates may already have completed. Check what completed before retrying the
runbook. After a successful run, list firmware components again to verify the
reported version.

Refreshing inventory on active nodes
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

With the ``redfish`` firmware interface, refresh the cached inventory of an
active node without inspection by running the ``cache_firmware_components``
service step::

    openstack --os-cloud iad3-dev baremetal node service <node> \
      --disable-ramdisk --service-steps \
      '[{"interface": "firmware", "step": "cache_firmware_components"}]'

This step reads firmware versions from Redfish and updates Ironic's cached
inventory, including PSU firmware when reported by the BMC. It does not install
firmware, boot a ramdisk, or reboot the server. The node temporarily enters
``servicing`` and returns to ``active`` after successful completion. Once it is
active again, view the refreshed inventory::

    openstack --os-cloud iad3-dev baremetal node firmware list <node>

The step uses the existing inventory collection behavior: unavailable optional
resources are logged and skipped, and old rows are not deleted. Check conductor
logs if an expected component is missing. An unhandled collection error fails
servicing and is reported in the node's ``last_error``.

How it works
------------

The ``update`` step can be used via cleaning or servicing, it accepts a JSON in
the following format::

    [{
        "interface": "firmware",
        "step": "update",
        "args": {
            "settings":[
                {
                    "component": "bmc",
                    "url": "<url_to_firmware_image1>",
                    "wait": <number_of_seconds_to_wait>
                },
                {
                    "component": "bios",
                    "url": "<url_to_firmware_image2>"
                },
                {
                    "component": "nic:AD0700",
                    "url": "<url_to_firmware_image3>"
                },
                {
                    "component": "nic:NIC.Slot.2",
                    "url": "<url_to_firmware_image4>"
                }
                ...
            ]
        }
    }]

The different attributes of the ``update`` step are as follows:

.. csv-table::
    :header: "Attribute", "Description"
    :widths: 30, 120

    "``interface``", "Interface of the step.  Must be ``firmware`` for firmware update"
    "``step``", "Name of the step.  Must be ``update`` for firmware update"
    "``args``", "Keyword-argument entry (<name>: <value>) being passed to the step"
    "``args.settings``", "Ordered list of dictionaries of firmware updates to be applied"

Each firmware image dictionary is of the form::

    {
      "component": "The desired component to have the firmware updated, supported components are listed below",
      "url": "<URL of firmware image file>",
      "wait": <Optional time in seconds to wait after applying update>
    }

.. csv-table::
    :header: "Supported Components", "Description"
    :widths: 30, 120

    "bmc", "The BMC firmware"
    "bios", "The BIOS firmware"
    "nic:<NIC_REDFISH_ID>", "Since machines can have multiple NICs, we use **nic:** as prefix plus the **NIC_REDFISH_ID** to identify the NIC to update"
    "firmware:<device>", "An additional component copied from the firmware inventory, for example **firmware:PSU.Slot.1**"

For ``firmware:`` components, the driver resolves the exact selector against
fresh Redfish firmware inventory before staging the image. The component must
identify one installed entry marked ``Updateable``. Missing, ambiguous and
non-updateable components are rejected. Current entries take precedence over
Installed entries, followed by generic IDs, as they do when listing inventory.
Shared firmware inventory entries covering multiple devices are supported
when the selector identifies one device. Matching entries with different
software IDs are rejected as ambiguous, even across priority levels.

The driver passes the selected ``RelatedItem`` URI as the ``SimpleUpdate``
target. If the entry has no ``RelatedItem``, it uses the firmware inventory
entry URI. The BMC must support updating that target through ``SimpleUpdate``;
the driver does not retry an unsuccessful targeted update without a target.
Additional components use the existing default monitoring and reboot handling.
Existing BIOS, BMC and NIC update behavior is unchanged.

Monitoring depends on the selector prefix. All ``firmware:`` selectors use
generic task monitoring and reboot handling, including selectors such as
``firmware:NIC.Integrated.1-1``. They do not use the specialized NIC handling
provided by ``nic:`` selectors. For BIOS, BMC and NIC updates, use the listed
``bios``, ``bmc`` or ``nic:`` selector when available. If only a ``firmware:``
selector is listed for a device, validate its update and reboot behavior on a
test node before using it in production runbooks.

For example, suppose the inventory lists both of these selectors for the same
physical NIC::

    nic:NIC.Embedded.1
    firmware:NIC.Embedded.2-1-1

Use the ``nic:`` selector in the runbook's update settings to retain specialized
NIC monitoring and reboot handling::

    [{"component": "nic:NIC.Embedded.1",
      "url": "https://firmware.example.com/nic-update.exe"}]

Using the following settings instead selects the firmware inventory target
and uses generic monitoring and reboot handling::

    [{"component": "firmware:NIC.Embedded.2-1-1",
      "url": "https://firmware.example.com/nic-update.exe"}]

The names alone do not prove that two selectors refer to the same physical
NIC. Confirm the device mapping in the BMC inventory before choosing between
them. If only the ``firmware:`` selector is listed, validate that update path
on a test node first.

For example, a runbook's firmware update step can use the following ``settings``
when the refreshed inventory lists ``firmware:PSU.Slot.1``::

    [{"component": "firmware:PSU.Slot.1",
      "url": "https://firmware.example.com/psu-update.exe"}]

This selects PSU slot 1, including when one firmware inventory entry covers
both PSU slots. If multiple matches remain at the preferred priority, or
matching entries report different software IDs, the update step fails with::

    Firmware component firmware:PSU.Slot.1 must identify exactly one installed firmware target.

The image for that component is not staged or submitted to the BMC. The runbook
stops at the failed step; previously completed updates are not rolled back.
Before retrying, refresh and list the node's firmware inventory, confirm the
exact component selector, and check the BMC's installed firmware inventory for
conflicting entries. Correct the selector or resolve the BMC inventory conflict
before retrying; changing the image URL does not resolve an ambiguous selector.

The ``component`` and ``url`` arguments in the firmware image dictionary are
mandatory, while the ``wait`` argument is optional.

For ``url`` currently ``http``, ``https``, ``swift`` and ``file`` schemes are
supported.

Applying updates
----------------

To perform a firmware update, first download the firmware to a web server,
Swift or filesystem that the Ironic conductor or BMC has network access to.
This could be the ironic conductor web server or another web server on the BMC
network. Using a web browser, curl, or similar tool on a server that has
network access to the BMC or Ironic conductor, try downloading the firmware to
verify that the URLs are correct and that the web server is configured
properly.

Next, construct the JSON for the firmware update step to be executed.
When launching the firmware update, the JSON may be specified on the command
line directly or in a file. The following example shows one step that
installs two firmware updates.

.. code-block:: json

    [{
        "interface": "firmware",
        "step": "update",
        "args": {
            "settings":[
                {
                    "component": "bmc",
                    "url": "http://192.0.2.10/BMC_4_22_00_00.EXE",
                    "wait": 300
                },
                {
                    "component": "bios",
                    "url": "https://192.0.2.10/BIOS_19.0.12_A00.EXE"
                },
                {
                    "component": "nic:AD0700",
                    "url": "http://192.0.2.10/NIC_19.0.12_AD0700.EXE"
                },
                {
                    "component": "nic:NIC.Slot.2",
                    "url": "http://192.0.2.10/NICSlot2_1.0.12.EXE"
                }
            ]
        }
    }]


It is also possible to use ``runbooks`` for firmware updates.

First, create a YAML file with the firmware update steps. For example, save the
following as ``firmware_runbook.yaml``:

.. code-block:: yaml

    - interface: firmware
      step: update
      args:
        settings:
          - component: bmc
            url: http://192.168.0.8:8080/ilo5278.bin
          - component: nic:AD0700
            url: http://192.168.0.8:8080/nic.bin
          - component: nic:NIC.Slot.2
            url: http://192.168.0.8:8080/nic.bin

Then create and use the runbook:

.. code-block:: console

    $ baremetal runbook create --name <RUNBOOK> --steps firmware_runbook.yaml
    $ baremetal node add trait <ironic_node_uuid> <RUNBOOK>
    $ baremetal node <clean or service>  <ironic_node_uuid> --runbook <RUNBOOK>

Finally, launch the firmware update step against the node. The
following example assumes the above JSON is in a file named
``update.json``:

.. code-block:: console

   $ baremetal node clean <ironic_node_uuid> --clean-steps update.json
   $ baremetal node service <ironic_node_uuid> --service-steps update.json

.. note::
   You can use the ``--disable-ramdisk`` flag to perform firmware updates
   without booting into the ramdisk, which saves reboots and speeds up the
   process:

   .. code-block:: console

      $ baremetal node clean <ironic_node_uuid> --disable-ramdisk --clean-steps update.json
      $ baremetal node service <ironic_node_uuid> --disable-ramdisk --service-steps update.json

In the following example, the JSON is specified directly on the command line:

.. code-block:: console

   $ baremetal node clean <ironic_node_uuid> --clean-steps \
       '[{"interface": "firmware", "step": "update", "args": {"settings":[{"component": "bmc", "url":"http://192.168.0.8:8080/ilo5278.bin"}]}}]'
   $ baremetal node clean <ironic_node_uuid> --clean-steps \
       '[{"interface": "firmware", "step": "update", "args": {"settings":[{"component": "bios", "url":"http://192.168.0.8:8080/bios.bin"}]}}]'
   $ baremetal node service <ironic_node_uuid> --service-steps \
       '[{"interface": "firmware", "step": "update", "args": {"settings":[{"component": "bmc", "url":"http://192.168.0.8:8080/ilo5278.bin"}]}}]'
   $ baremetal node service <ironic_node_uuid> --service-steps \
       '[{"interface": "firmware", "step": "update", "args": {"settings":[{"component": "bios", "url":"http://192.168.0.8:8080/bios.bin"}]}}]'
   $ baremetal node clean <ironic_node_uuid> --clean-steps \
       '[{"interface": "firmware", "step": "update", "args": {"settings":[{"component": "nic:AD0700", "url":"http://192.168.0.8:8080/nic.bin"}]}}]'
   $ baremetal node clean <ironic_node_uuid> --clean-steps \
       '[{"interface": "firmware", "step": "update", "args": {"settings":[{"component": "nic:NIC.Slot.2", "url":"http://192.168.0.8:8080/nic.bin"}]}}]'

.. note::
   For Dell machines, you must extract the firmimgFIT.d9 from the iDRAC.exe
   This can be done using the command ``7za e iDRAC_<VERSION>.exe``.

.. note::
   For HPE machines you must extract the ilo5_<version>.bin from the
   ilo5_<version>.fwpkg
   This can be done using the command ``7za e ilo<version>.fwpkg``.

How it works - via Management Interface
---------------------------------------

.. deprecated::
   The management interface for firmware updates is deprecated in favor of
   the firmware interface. Please use the firmware interface for new
   deployments.

The ``update_firmware`` cleaning step accepts JSON in the following format::

    [{
        "interface": "management",
        "step": "update_firmware",
        "args": {
            "firmware_images":[
                {
                    "url": "<url_to_firmware_image1>",
                    "checksum": "<checksum for image, uses SHA1, SHA256, or SHA512>",
                    "source": "<optional override source setting for image>",
                    "wait": <number_of_seconds_to_wait>
                },
                {
                    "url": "<url_to_firmware_image2>"
                },
                ...
            ]
        }
    }]

The different attributes of the ``update_firmware`` cleaning step are as follows:

.. csv-table::
    :header: "Attribute", "Description"
    :widths: 30, 120

    "``interface``", "Interface of the cleaning step.  Must be ``management`` for firmware update"
    "``step``", "Name of cleaning step.  Must be ``update_firmware`` for firmware update"
    "``args``", "Keyword-argument entry (<name>: <value>) being passed to the step"
    "``args.firmware_images``", "Ordered list of dictionaries of firmware images to be applied"

Each firmware image dictionary is of the form::

    {
      "url": "<URL of firmware image file>",
      "checksum": "<checksum for image, uses SHA1>",
      "source": "<Optional override source setting for image>",
      "wait": <Optional time in seconds to wait after applying update>
    }

The ``url`` and ``checksum`` arguments in the firmware image dictionary are
mandatory, while the ``source`` and ``wait`` arguments are optional.

For ``url`` currently ``http``, ``https``, ``swift`` and ``file`` schemes are
supported.

``source`` corresponds to :oslo.config:option:`redfish.firmware_source` and by
setting it here, it is possible to override global setting per firmware image
in clean step arguments.

.. note::
   At the present time, targets for the firmware update cannot be specified.
   In testing, the BMC applied the update to all applicable targets on the
   node. It is assumed that the BMC knows what components a given firmware
   image is applicable to.

To perform a firmware update, first download the firmware to a web server,
Swift or filesystem that the Ironic conductor or BMC has network access to.
This could be the ironic conductor web server or another web server on the BMC
network. Using a web browser, curl, or similar tool on a server that has
network access to the BMC or Ironic conductor, try downloading the firmware to
verify that the URLs are correct and that the web server is configured
properly.

Next, construct the JSON for the firmware update cleaning step to be executed.
When launching the firmware update, the JSON may be specified on the command
line directly or in a file. The following example shows one cleaning step that
installs four firmware updates. All except 3rd entry that has explicit
``source`` added, uses the setting from :oslo.config:option:`redfish.firmware_source`
to determine if and where to stage the files:

.. code-block:: json

    [{
        "interface": "management",
        "step": "update_firmware",
        "args": {
            "firmware_images":[
                {
                    "url": "http://192.0.2.10/BMC_4_22_00_00.EXE",
                    "checksum": "<sha1-checksum-of-the-file>",
                    "wait": 300
                },
                {
                    "url": "https://192.0.2.10/NIC_19.0.12_A00.EXE",
                    "checksum": "<sha1-checksum-of-the-file>"
                },
                {
                    "url": "file:///firmware_images/idrac/9/PERC_WN64_6.65.65.65_A00.EXE",
                    "checksum": "<sha1-checksum-of-the-file>",
                    "source": "http"
                },
                {
                    "url": "swift://firmware_container/BIOS_W8Y0W_WN64_2.1.7.EXE",
                    "checksum": "<sha1-checksum-of-the-file>"
                }
            ]
        }
    }]

Finally, launch the firmware update cleaning step against the node. The
following example assumes the above JSON is in a file named
``firmware_update.json``:

.. code-block:: console

   $ baremetal node clean <ironic_node_uuid> --clean-steps firmware_update.json

In the following example, the JSON is specified directly on the command line:

.. code-block:: console

   $ baremetal node clean <ironic_node_uuid> --clean-steps \
       '[{"interface": "management", "step": "update_firmware", "args": {"firmware_images":[{"url": "http://192.0.2.10/BMC_4_22_00_00.EXE", "wait": 300}, {"url": "https://192.0.2.10/NIC_19.0.12_A00.EXE"}]}}]'

.. note::
   Firmware updates may take some time to complete. If a firmware update
   cleaning step consistently times out, then consider performing fewer
   firmware updates in the cleaning step or increasing
   ``clean_callback_timeout`` in ironic.conf to increase the timeout value.

.. warning::
   Warning: Removing power from a server while it is in the process of updating
   firmware may result in devices in the server, or the server itself becoming
   inoperable.


Out-of-band cleaning
====================

The firmware clean step can execute without booting a ramdisk. The service
step already supports out-of-band updates. Some NIC updates require a running
OS for Redfish inventory visibility; satisfy that platform prerequisite
before requesting those components.
