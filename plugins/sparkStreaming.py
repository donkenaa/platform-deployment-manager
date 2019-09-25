"""
Name:       sparkStreaming.py
Purpose:    Submits spark streaming jobs based on application package
Author:     PNDA team

Created:    21/03/2016

Copyright (c) 2016 Cisco and/or its affiliates.

This software is licensed to you under the terms of the Apache License, Version 2.0 (the "License").
You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0

The code, technical concepts, and all information contained herein, are the property of Cisco Technology, Inc.
and/or its affiliated entities, under various laws including copyright, international treaties, patent,
and/or contract. Any use of the material herein must be in accordance with the terms of the License.
All rights not expressly granted by the License are reserved.

Unless required by applicable law or agreed to separately in writing, software distributed under the
License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND,
either express or implied.
"""

# pylint: disable=C0103

import json
import os
import logging
from shutil import copy
import deployer_utils
from plugins.base_common import Common


class SparkStreamingCreator(Common):

    def validate_component(self, component):
        errors = []
        file_list = component['component_detail']
        if 'application.properties' not in file_list:
            errors.append('missing file application.properties')
        if 'log4j.properties' not in file_list:
            errors.append('missing file log4j.properties')
        if 'upstart.conf' in file_list:
            errors.append('Support for user supplied upstart.conf files has been deprecated, ' +
                          'the deployment manager will supply one automatically. ' +
                          'Please see PNDA example-applications for usage.')
        return errors

    def get_component_type(self):
        return 'sparkStreaming'

    def create_component(self, staged_component_path, application_name, user_name, component, properties):
        logging.debug("create_component: %s %s %s %s", application_name, user_name, json.dumps(component), properties)
        remote_component_tmp_path = '%s/%s/%s' % (
            '/tmp/%s' % self._namespace, application_name, component['component_name'])
        remote_component_install_path = '%s/%s/%s' % (
            '/opt/%s' % self._namespace, application_name, component['component_name'])
        service_name = '%s-%s-%s' % (self._namespace, application_name, component['component_name'])

        key_file = self._environment['cluster_private_key']
        root_user = self._environment['cluster_root_user']
        target_host = 'localhost'

        if 'component_spark_version' not in properties:
            properties['component_spark_version'] = '1'
        if 'component_spark_submit_args' not in properties:
            properties['component_spark_submit_args'] = ''
        if 'component_py_files' not in properties:
            properties['component_py_files'] = ''

        if 'upstart.conf' in component['component_detail']:
            # old style applications - reject these
            raise Exception('Support for user supplied upstart.conf files has been deprecated, ' +
                            'the deployment manager will supply one automatically. ' +
                            'Please see PNDA example-applications for usage.')
        else:
            # new style applications that don't need to provide upstart.conf or yarn-kill.py
            if 'component_main_jar' in properties and 'component_main_class' not in properties:
                raise Exception('properties.json must contain "main_class" for %s sparkStreaming %s' % (application_name, component['component_name']))

            java_app = None
            if 'component_main_jar' in properties:
                java_app = True
            elif 'component_main_py' in properties:
                java_app = False
            else:
                raise Exception('properties.json must contain "main_jar or main_py" for %s sparkStreaming %s' % (application_name, component['component_name']))

            this_dir = os.path.dirname(os.path.realpath(__file__))
            copy(os.path.join(this_dir, 'yarn-kill.py'), staged_component_path)
            service_script = 'systemd.service.tpl' if java_app else 'systemd.service.py.tpl'
            service_script_install_path = '/usr/lib/systemd/system/%s.service' % service_name
            if 'component_respawn_type' not in properties:
                properties['component_respawn_type'] = 'always'
            if 'component_respawn_timeout_sec' not in properties:
                properties['component_respawn_timeout_sec'] = '2'
            copy(os.path.join(this_dir, service_script), staged_component_path)

        self._fill_properties(os.path.join(staged_component_path, service_script), properties)
        self._fill_properties(os.path.join(staged_component_path, 'log4j.properties'), properties)
        self._fill_properties(os.path.join(staged_component_path, 'application.properties'), properties)
        self._fill_properties(os.path.join(staged_component_path, 'yarn-kill.py'), properties)

        mkdircommands = []
        mkdircommands.append('mkdir -p %s' % remote_component_tmp_path)
        mkdircommands.append('sudo mkdir -p %s' % remote_component_install_path)
        deployer_utils.exec_ssh(target_host, root_user, key_file, mkdircommands)

        os.system("scp -i %s -o StrictHostKeyChecking=no %s %s@%s:%s"
                  % (key_file, staged_component_path + '/*', root_user, target_host, remote_component_tmp_path))

        for node in self._environment['yarn_node_managers'].split(','):
            deployer_utils.exec_ssh(node, root_user, key_file, ['mkdir -p %s' % remote_component_tmp_path])
            os.system("scp -i %s -o StrictHostKeyChecking=no %s %s@%s:%s"
                      % (key_file, staged_component_path + '/log4j.properties', root_user, node, remote_component_tmp_path + '/log4j.properties'))
            deployer_utils.exec_ssh(node, root_user, key_file,
                                    ['sudo mkdir -p %s' % remote_component_install_path,
                                     'sudo mv %s %s' % (remote_component_tmp_path + '/log4j.properties', remote_component_install_path + '/log4j.properties')])

        commands = []
        commands.append('sudo cp %s/%s %s' % (remote_component_tmp_path, service_script, service_script_install_path))
        commands.append('sudo cp %s/* %s' % (remote_component_tmp_path, remote_component_install_path))
        commands.append('sudo chmod a+x %s/yarn-kill.py' % (remote_component_install_path))
        if 'component_main_jar' in properties:
            commands.append('cd %s && sudo jar uf %s application.properties' % (remote_component_install_path, properties['component_main_jar']))
        commands.append('sudo rm -rf %s' % (remote_component_tmp_path))
        deployer_utils.exec_ssh(target_host, root_user, key_file, commands)

        undo_commands = []
        undo_commands.append('sudo service %s stop\n' % service_name)
        undo_commands.append('sudo rm -rf %s\n' % remote_component_install_path)
        undo_commands.append('sudo rm  %s\n' % service_script_install_path)
        logging.debug("uninstall commands: %s", undo_commands)

        start_commands = []
        start_commands.append('sudo systemctl daemon-reload\n')
        start_commands.append('sudo service %s start\n' % service_name)
        logging.debug("start commands: %s", start_commands)

        stop_commands = []
        stop_commands.append('sudo service %s stop\n' % service_name)
        logging.debug("stop commands: %s", stop_commands)

        return {'ssh': undo_commands, 'start_cmds': start_commands, 'stop_cmds': stop_commands}
