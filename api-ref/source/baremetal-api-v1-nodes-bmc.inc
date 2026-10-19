.. -*- rst -*-

================
Node BMC (nodes)
================

.. versionadded:: 1.116

Given a Node identifier (``uuid`` or ``name``), the API exposes the list of
all BMC (management controller) settings associated with that Node.

BMC settings are structurally identical to Bios settings, but describe the
configuration of the management controller itself rather than the host
firmware. Access is restricted more tightly than Bios settings: by default
only the system and the node's owner administrator may read them (see the
``baremetal:node:bmc:get`` policy).

These endpoints do not allow modification of the BMC settings; that should be
done by using ``clean steps``.

List all BMC settings by Node
=============================

.. rest_method:: GET /v1/nodes/{node_ident}/bmc

Return a list of BMC settings associated with ``node_ident``.

The additional fields from the BMC registry can be retrieved using
``?detail=True`` (see detailed response below). A ``fields`` selector can be
used to query for particular fields.

Normal response code: 200

Error codes: 403, 404

Request
-------

.. rest_parameters:: parameters.yaml

    - node_ident: node_ident
    - fields: fields
    - detail: detail

Response
--------

.. rest_parameters:: parameters.yaml

    - bmc: bmc_settings
    - created_at: created_at
    - updated_at: updated_at
    - links: links
    - name: bmc_setting_name
    - value: bmc_setting_value

**Example list of a Node's BMC settings:**

.. literalinclude:: samples/node-bmc-list-response.json

List detailed BMC settings by Node
==================================

.. rest_method:: GET /v1/nodes/{node_ident}/bmc/?detail=True

Return a list of detailed BMC settings associated with ``node_ident``.
The detailed list includes the BMC Attribute Registry information
retrieved via Redfish.

.. versionadded:: 1.116
  Introduced

Normal response code: 200

Error codes: 403, 404

Request
-------

.. rest_parameters:: parameters.yaml

    - node_ident: node_ident

Response
--------

.. rest_parameters:: parameters.yaml

    - bmc: bmc_settings
    - created_at: created_at
    - updated_at: updated_at
    - links: links
    - name: bmc_setting_name
    - value: bmc_setting_value
    - attribute_type: bmc_setting_attribute_type
    - allowable_values: bmc_setting_allowable_values
    - lower_bound: bmc_setting_lower_bound
    - max_length: bmc_setting_max_length
    - min_length: bmc_setting_min_length
    - read_only: bmc_setting_read_only
    - reset_required: bmc_setting_reset_required
    - unique: bmc_setting_unique
    - upper_bound: bmc_setting_upper_bound

**Example list of a Node's detailed BMC settings:**

.. literalinclude:: samples/node-bmc-list-details-response.json


Show single BMC setting of a Node
=================================

.. rest_method:: GET /v1/nodes/{node_ident}/bmc/{bmc_setting}

Return the content of the specific BMC ``bmc_setting`` associated with
``node_ident``.

.. versionadded:: 1.116
  Introduced

Normal response code: 200

Error codes: 403, 404

Request
-------

.. rest_parameters:: parameters.yaml

    - node_ident: node_ident
    - bmc_setting: bmc_setting

Response
--------

.. rest_parameters:: parameters.yaml

    - <key>: d_bmc_setting
    - created_at: created_at
    - updated_at: updated_at
    - links: links
    - name: bmc_setting_name
    - value: bmc_setting_value
    - attribute_type: bmc_setting_attribute_type
    - allowable_values: bmc_setting_allowable_values
    - lower_bound: bmc_setting_lower_bound
    - max_length: bmc_setting_max_length
    - min_length: bmc_setting_min_length
    - read_only: bmc_setting_read_only
    - reset_required: bmc_setting_reset_required
    - unique: bmc_setting_unique
    - upper_bound: bmc_setting_upper_bound

**Example details of a Node's BMC setting details:**

.. literalinclude:: samples/node-bmc-detail-response.json
