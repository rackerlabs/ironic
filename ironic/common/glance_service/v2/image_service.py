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

from ironic.common import exception as exc
from ironic.common.glance_service import base_image_service
from ironic.common.glance_service import service
from ironic.common.glance_service import service_utils

from oslo.config import cfg
import time
import uuid


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
                help='The base URL for the Swift server.'),
    cfg.StrOpt('swift_api_version',
                default='v1',
                help='The API string to prepend to all API URLs.'),
    cfg.StrOpt('swift_backend_container',
                help='The name of the Swift container Glance stores its '
                     'images in.')
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

    def swift_temp_url(self, image_id, duration=None):
        """Returns a temporary URL that is good for duration seconds. This
        allows Ironic to download a Glance image without passing around an
        auth_token. If glance has 'show_image_direct_url' enabled, we will
        use the URL from that to find the Swift URL. Otherwise, we will infer
        the URL from the Glance image-id.

        :param image_id: The opaque image identifier.
        :returns: A signed Swift URL that can be downloaded from without auth.

        :raises: ImageNotFound, InvalidParameterValue, ImageUnacceptable
        """
        self._validate_temp_url_config()
        duration = duration or CONF.glance.swift_temp_url_duration
        if duration is None:
            raise exc.InvalidParameterValue('Must either call with a duration '
                                            'or set the config option '
                                            'swift_temp_url_duration')
        # Can raise ImageNotFound, let it bubble up
        object_name = self._get_object_name(image_id)

        template = '{swift_version}/{container}/{object_name}'
        url_path = template.format(
            swift_version=CONF.glance.swift_api_version,
            container=CONF.glance.swift_backend_container,
            object_name=object_name
        )

        # NOTE(pcsforeducation) timezone issues?
        expiration = int(time.time() + duration)
        methods = ' '.join(CONF.glance.swift_temp_url_methods)
        hmac_body = '\n'.join([methods, str(expiration), url_path])
        key = CONF.glance.swift_temp_url_key
        sig = hmac.new(key, hmac_body, hashlib.sha1).hexdigest()
        # TODO(pcsforeducation) should be a way to detect this from direct_url
        host = CONF.glance.swift_base_url.rstrip('/')
        return '{host}/{url}?temp_url_sig={sig}&temp_url_expires={exp}'.format(
            host=host, url=url_path, sig=sig, exp=expiration
        )

    def _validate_temp_url_config(self):
        """Validate all the settings for a temp url."""
        if not CONF.glance.swift_temp_url_key:
            raise exc.InvalidParameterValue('Swift temp urls require a temp '
                                            'url key to sign the URL.')
        try:
            int(CONF.glance.swift_temp_url_duration)
        except (ValueError, TypeError):
            raise exc.InvalidParameterValue(
                'swift_temp_url_duration must be an integer.')
        methods = CONF.glance.swift_temp_url_methods
        if not methods:
            raise exc.InvalidParameterValue('Must have at least one HTTP '
                                            'method.')

        for method in methods:
            if method not in ['GET', 'HEAD', 'PUT', 'POST', 'DELETE']:
                raise exc.InvalidParameterValue(
                    'swift_temp_url_methods must be in ["GET", "HEAD", "PUT", '
                    '"POST", "DELETE"]')
        if not CONF.glance.swift_base_url:
            raise exc.InvalidParameterValue('Must have a Swift base URL.')

    def _get_object_name(self, image_id):
        # Can raise ImageNotFound, let it bubble up
        direct_url = self._get_location(image_id)

        if direct_url is None:
            raise exc.ImageUnacceptable(
                'Could not find an object name in either direct_url or '
                'inferring from the image_id. You need to either enable'
                'direct_url in /etc/glance/glance-api.conf by setting '
                '"show_image_direct_url = True" or providing a valid '
                'image_id.')
        object_name = self._get_temp_url_filename(direct_url)
        # Glance defaults to using UUIDs for swift object names.
        try:
            uuid.UUID(object_name)
        except ValueError:
            raise exc.ImageUnacceptable('The object name {0} in the '
                                        'direct_url isn\'t a valid UUID.'
                                        .format(object_name))
        return object_name

    def _get_temp_url_filename(self, url):
        """Get the object filename from the direct_url from Glance."""
        #TODO(pcsforeducation) make this more robust
        try:
            # Parse out filename from glance url
            return url.split('/')[-1]
        except IndexError as e:
            raise exc.ImageUnacceptable(
                'Image direct URL {0} improperly formatted, exception: {1}'
                .format(url, str(e))
            )

    def _get_location(self, image_id):
        """Returns the direct url representing the backend storage location,
        or None if this attribute is not shown by Glance.
        """
        image_meta = self.call('get', image_id)

        if not service_utils.is_image_available(self.context, image_meta):
            raise exc.ImageNotFound(image_id=image_id)

        return getattr(image_meta, 'direct_url', None)
