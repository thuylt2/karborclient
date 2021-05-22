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

from karborclient.common.apiclient import exceptions
from karborclient.common import base


class Trigger(base.Resource):
    def __repr__(self):
        return "<Trigger %s>" % self._info


class TriggerManager(base.ManagerWithFind):
    resource_class = Trigger

    def create(self, name, type, properties):
        if properties.get('window', None):
            try:
                properties['window'] = int(properties['window'])
            except Exception:
                msg = 'The trigger window is not integer'
                raise exceptions.CommandError(msg)
        body = {'trigger_info': {'name': name,
                                 'type': type,
                                 'properties': properties,
                                 }}
        url = "/triggers"
        return self._create(url, body, 'trigger_info')

    def delete(self, trigger_id):
        path = '/triggers/{trigger_id}'.format(
            trigger_id=trigger_id)
        return self._delete(path)

    def get(self, trigger_id, session_id=None):
        if session_id:
            headers = {'X-Configuration-Session': session_id}
        else:
            headers = {}
        url = "/triggers/{trigger_id}".format(
            trigger_id=trigger_id)
        return self._get(url, response_key="trigger_info", headers=headers)

    def update(self, trigger_id, data):

        if data['properties'].get('window', None):
            try:
                data['properties']['window'] = int(
                    data['properties']['window'])
            except Exception:
                msg = 'The trigger window is not integer'
                raise exceptions.CommandError(msg)
        body = {"trigger_info": data}
        return self._update('/triggers/{trigger_id}'
                            .format(trigger_id=trigger_id),
                            body, "trigger_info")

    def list(self, detailed=False, search_opts=None, marker=None, limit=None,
             sort_key=None, sort_dir=None, sort=None):
        """Lists all triggers."""

        resource_type = "triggers"
        url = self._build_list_url(
            resource_type, detailed=detailed,
            search_opts=search_opts, marker=marker,
            limit=limit, sort_key=sort_key,
            sort_dir=sort_dir, sort=sort)
        return self._list(url, 'triggers')
