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

import hashlib
import hmac
import time

from oslo.config import cfg
import six.moves.urllib.parse as urlparse

from ironic.common import exception as exc
from ironic.common.glance_service import base_image_service
from ironic.common.glance_service import service
from ironic.common.glance_service import service_utils
from ironic.common import utils


glance_opts = [
    cfg.ListOpt('allowed_direct_url_schemes',
                default=[],
                help='A list of URL schemes that can be downloaded directly '
                'via the direct_url.  Currently supported schemes: '
                '[file].'),
    # To upload this key to Swift:
    # swift post -m Temp-Url-Key:correcthorsebatterystaple
    cfg.StrOpt('swift_temp_url_key',
                help='The secret token given to Swift to allow temporary URL '
                     'downloads.'),
    cfg.ListOpt('swift_temp_url_methods',
                default=['GET'],
                help='A list of HTTP methods allowed for the temporary URL in '
                     'Swift.'),
    cfg.IntOpt('swift_temp_url_duration',
                default=3600,
                help='The length of time in seconds that the temporary URL '
                     'will be valid for.'),
    cfg.StrOpt('swift_base_url',
                help='Override the base URL for the Swift server (without '
                     'scheme). Should not have a trailing "/".'),
    cfg.StrOpt('swift_api_version',
                help='Override the API string to prepend to all API URLs. '
                     'e.g. "v2.0".'),
    cfg.StrOpt('swift_backend_container',
                help='Override the name of the Swift container Glance stores '
                     'its images in.')
]

CONF = cfg.CONF
CONF.register_opts(glance_opts, group='glance')


class GlanceImageService(base_image_service.BaseImageService,
                         service.ImageService):

    def detail(self, **kwargs):
        return self._detail(method='list', **kwargs)

    def show(self, image_id):
        return self._show(image_id, method='get')

    def download(self, image_id, data=None):
        return self._download(image_id, method='data', data=data)

    def create(self, image_meta, data=None):
        image_id = self._create(image_meta, method='create', data=None)['id']
        return self.update(image_id, None, data)

    def update(self, image_id, image_meta, data=None, purge_props=False):
        # NOTE(ghe): purge_props not working until bug 1206472 solved
        return self._update(image_id, image_meta, data, method='update',
                            purge_props=False)

    def delete(self, image_id):
        return self._delete(image_id, method='delete')

    def swift_temp_url(self, image_info, duration=None):
        """Returns a temporary URL that is good for duration seconds. This
        allows Ironic to download a Glance image without passing around an
        auth_token. If glance has 'show_image_direct_url' enabled, we will
        use the URL from that to find the Swift URL. Otherwise, we will infer
        the URL from the Glance image-id.

        :param image_info: The return from a GET request to Glance for a
        certain image_id
        :returns: A signed Swift URL that can be downloaded from without auth.

        :raises: ImageNotFound, InvalidParameterValue, ImageUnacceptable
        """
        self._validate_temp_url_config()
        duration = duration or CONF.glance.swift_temp_url_duration
        if duration is None:
            raise exc.InvalidParameterValue(_('Must either call with a '
                                              'duration or set the config '
                                              'option '
                                              'swift_temp_url_duration'))
         # Can raise ImageNotFound, let it bubble up
        if not image_info.get('direct_url'):
            raise exc.ImageUnacceptable(_('Images must have a direct_url to '
                                          'use swift temp urls.'))

        direct_url = image_info['direct_url']

         # Can raise ImageUnacceptable, let it bubble up
        url_fragments = self._swift_url_fragments(direct_url)

        template = '{swift_version}/{container}/{object_id}'
        url_path = template.format(
            swift_version=url_fragments['api_version'],
            container=url_fragments['container'],
            object_id=url_fragments['object_id']
        )

        expiration = int(time.time() + duration)
        methods = ' '.join(CONF.glance.swift_temp_url_methods)
        hmac_body = '\n'.join([methods, str(expiration), url_path])
        key = CONF.glance.swift_temp_url_key
        # Encode to UTF-8
        try:
            sig = hmac.new(key.encode(),
                           hmac_body.encode(),
                           hashlib.sha1).hexdigest()
        except UnicodeDecodeError:
            raise exc.InvalidParameterValue(_('Could not convert '
                                              'swift temp url arguments '
                                              'to Unicode for url.'))
        return ('http://{host}/{url}?temp_url_sig={sig}&temp_url_expires='
                '{exp}'.format(
                    host=url_fragments['host'],
                    url=url_path,
                    sig=sig,
                    exp=expiration)
                )

    def _validate_temp_url_config(self):
        """Validate all the settings for a temp url."""
        if not CONF.glance.swift_temp_url_key:
            raise exc.InvalidParameterValue(_('Swift temp urls require a temp '
                                              'url key to sign the URL.'))
        methods = CONF.glance.swift_temp_url_methods
        if not methods:
            raise exc.InvalidParameterValue(_('Must have at least one HTTP '
                                              'method.'))

        valid_methods = ['GET', 'HEAD', 'PUT', 'POST', 'DELETE']
        for method in methods:
            if method not in valid_methods:
                raise exc.InvalidParameterValue(
                    _('swift_temp_url_methods must be in %s') % valid_methods)

    def _swift_url_fragments(self, direct_url):
        parsed = urlparse.urlparse(direct_url)

        # Remove username/password if they exist.
        swift_host = parsed.netloc.split('@')[-1]

        try:
            empty, api_version, container, object_id = parsed.path.split('/')
        except ValueError:
            raise exc.ImageUnacceptable(_('The direct URL could not be '
                                          'decoded'))
        if not utils.is_uuid_like(object_id):
            raise exc.ImageUnacceptable(_('The object name %s in the '
                                          'direct_url isn\'t a valid UUID.') %
                                          object_id)

        return {
            'host': CONF.glance.swift_base_url or swift_host,
            'scheme': parsed.scheme,
            'api_version': CONF.glance.swift_api_version or api_version,
            'container': CONF.glance.swift_backend_container or container,
            'object_id': object_id,
        }

    def _get_location(self, image_id):
        """Returns the direct url representing the backend storage location,
        or None if this attribute is not shown by Glance.
        """
        image_meta = self.call('get', image_id)

        if not service_utils.is_image_available(self.context, image_meta):
            raise exc.ImageNotFound(image_id=image_id)

        return getattr(image_meta, 'direct_url', None)
