.. _snmpv3-credential-derivation:

Per-node SNMPv3 credential derivation
=====================================

The ``ironic.common.snmpv3`` module derives a username and separate SNMPv3
authentication and privacy passphrases from one master secret and a Dell
service tag. Trusted provisioning consumers can reproduce the credentials
without storing a separate passphrase for every node. The helper uses only
the Python standard library and can run without conductor configuration,
database access, or a running Ironic service.

This utility derives credentials only. It does not configure BMC users,
register conductor configuration options, or provision monitoring systems.
Consumers are responsible for applying the same credentials to the BMC and
the monitoring system, and for choosing the SNMPv3 protocols and account slot.

Inputs and outputs
------------------

Supply a dedicated, high-entropy master secret of at least 32 characters.
Keep it separate from root-password masters and other credentials. The
minimum length check does not measure entropy; generate the master with a
cryptographically secure generator and store it in a protected secret store.

The identity must be a seven-character Dell service tag containing ASCII
letters or digits. Surrounding whitespace is stripped and letters are
converted to uppercase. Missing or invalid tags are rejected. There is no
BMC IP address or Ironic node UUID fallback.

``derive_credentials(master_key, service_tag, prefix='uc')`` returns a
dictionary with:

* ``username``: ``<prefix>-<lowercase service tag>`` (default prefix ``uc``).
* ``auth_passphrase``: a 32-character lowercase hexadecimal string.
* ``privacy_passphrase``: a separately derived string of the same format.

The optional ``prefix`` must contain 1 to 16 lowercase ASCII letters or digits
and defaults to ``uc``. It changes only the username; the auth and privacy
passphrases are prefix-independent, so changing the prefix never changes them.

The same master and canonical tag produce the same credentials. Different
nodes and purposes have separate derivation inputs. Changing the BMC address
does not change the credentials; changing its service tag does.

Version 1 contract
------------------

Other provisioning implementations must follow this contract exactly:

#. Canonicalize the service tag as described above.
#. Encode the master as UTF-8 text, without hexadecimal or Base64 decoding.
#. Compute HMAC-SHA256 using the master as key and the ASCII message
   ``ironic-snmpv3:v1:<purpose>:<SERVICE_TAG>``. The purpose is ``auth`` or
   ``privacy`` and the service tag is uppercase.
#. Use the first 32 lowercase hexadecimal characters of each digest as the
   respective passphrase. This is hexadecimal truncation, not 32 raw bytes.

The prefix, version, purposes, encoding, normalization, and truncation are
part of the contract. Changing any of them changes existing credentials.

The following public test vector verifies compatible implementations. Never
use its master in a deployment:

.. code-block:: text

   master:             0123456789abcdef0123456789abcdef
   service tag:        ABC1234
   username:           uc-abc1234
   auth passphrase:    b42a89a8e493e33f1c9c6e7a4c2eea51
   privacy passphrase: eb9a69944a64bab95a6e3f67e7c9a745

Python API
----------

Pass the master from the consumer's protected secret source and the service
tag from its inventory to the helper:

.. code-block:: python

   from ironic.common import snmpv3

   credentials = snmpv3.derive_credentials(master_key, service_tag)
   # Or override the default username prefix:
   #   snmpv3.derive_credentials(master_key, service_tag, prefix='uc')
   # Pass credentials directly to the provisioning client; do not log them.

Invalid inputs raise ``ValueError`` without including the master value.

Lookup command
--------------

Create a protected UTF-8 INI file with this structure, substituting the real
master through your secret-management system:

.. code-block:: ini

   [drac]
   snmpv3_master_key = <dedicated high-entropy master secret>

These names define the lookup command's input format; the utility does not
register a ``[drac]snmpv3_master_key`` option with Ironic's configuration
system. The command reads only the supplied file, with interpolation disabled.

From an environment where the Ironic package is available, run:

.. code-block:: console

   $ python -m ironic.common.snmpv3 ABC1234 --config-file /protected/snmpv3.conf

Pass ``--prefix`` to override the default ``uc`` username prefix; it accepts 1
to 16 lowercase ASCII letters or digits and changes only the username.

A successful lookup writes one JSON object to stdout with the three fields
listed above. Invalid input or an unreadable config file exits with status 2
and a generic error on stderr, without printing credentials.

The JSON contains secret values. Feed it directly to a trusted provisioning
consumer; do not send it to workflow logs, shell tracing, or tickets. Keep
the master out of command-line arguments. Access to the master permits
deriving credentials for every node that uses it, so restrict access to
trusted provisioning services.

Master lifecycle
----------------

Persist and back up the master before applying derived credentials to BMCs.
Reusing it makes lookups stable across process restarts and repeated
provisioning. Losing or replacing it changes all derived passphrases.
Coordinate master rotation with BMC and monitoring credential updates.
A service-tag correction requires updating both systems for that node.
