#!/usr/bin/env python
# coding: utf-8

# A restful HTTP API for ansible by tornado
# Base on ansible 2.x
# Github <https://github.com/lfbear/ansible-api>
# Author: lfbear, pgder

from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

import os
import time
from concurrent.futures import ThreadPoolExecutor

import yaml
import tornado.gen
import tornado.ioloop
from jinja2 import Environment, meta
from tornado.web import RequestHandler, HTTPError

from ansible_api.tool import Tool
from ansible_api.config import Config
from ansible_api.core import Api


__all__ = [
    'Main',
    'FileList',
    'FileReadWrite',
    'FileExist',
    'ParseVarsFromFile',
    'Command',
    'Playbook',
    'AsyncTest',
]

# Create two ThreadPoolExecutor to handle high load requests and normal requests
AsyncPool = ThreadPoolExecutor(int(Config.Get('thread_pool_size')))
SyncPool = ThreadPoolExecutor(int(Config.Get('thread_pool_size')))


class ErrorCode(object):
    ERRCODE_NONE = 0
    ERRCODE_SYS = 1
    ERRCODE_BIZ = 2


class Controller(RequestHandler):

    def __init__(self, application, request, **kwargs):
        Tool.LOGGER.debug("MORE DETAIL: request %s" % request)
        super(Controller, self).__init__(application, request, **kwargs)
        if len(Config.Get('allow_ip')) and self.request.remote_ip not in Config.Get('allow_ip'):
            raise HTTPError(403, 'Your ip(%s) is forbidden' % self.request.remote_ip)


class Main(Controller):

    def get(self):
        self.finish(Tool.jsonal(
            {'message': "Hello, I am Ansible Api", 'rc': ErrorCode.ERRCODE_NONE}))


class AsyncTest(Controller):

    async def get(self):
        msg = await tornado.ioloop.IOLoop.current().run_in_executor(SyncPool, self.test, 10)
        self.finish(Tool.jsonal(
            {'message': msg, 'rc': ErrorCode.ERRCODE_NONE}))

    def test(self, s):
        time.sleep(s)
        return 'i have slept 10 s'


class Command(Controller):

    def get(self):
        self.finish(Tool.jsonal(
            {'error': "Forbidden in get method", 'rc': ErrorCode.ERRCODE_SYS}))

    async def post(self):    # Change the async method to python3 async, this performance better than gen.coroutine
        data = Tool.parsejson(self.request.body)
        badcmd = ['reboot', 'su', 'sudo', 'dd',
                  'mkfs', 'shutdown', 'half', 'top']
        name = data['n'].encode('utf-8').decode()
        module = data['m']
        arg = data['a'].encode('utf-8').decode()
        target = data['t']
        sign = data['s']
        sudo = True if data['r'] else False
        mode = data.get('i', False) #True for async
        forks = data.get('c', 50)
        cmdinfo = arg.split(' ', 1)
        Tool.LOGGER.info('run: {0}, {1}, {2}, {3}, {4}, {5}'.format(
            name, target, module, arg, sudo, forks))
        hotkey = name + module + target + Config.Get('sign_key')
        check_str = Tool.getmd5(hotkey)
        if sign != check_str:
            self.finish(Tool.jsonal(
                {'error': "Sign is error", 'rc': ErrorCode.ERRCODE_BIZ}))
        else:
            if module in ('shell', 'command') and cmdinfo[0] in badcmd:
                self.finish(Tool.jsonal(
                    {'error': "This is danger shell: " + cmdinfo[0], 'rc': ErrorCode.ERRCODE_BIZ}))
            else:
                host_list = target.split(",")
                if mode:
                    self.finish({'rc': ErrorCode.ERRCODE_NONE, 'async': True })  # async execute task release http connection
                    await tornado.ioloop.IOLoop.current().run_in_executor(
                        AsyncPool, Api.run_cmd, name, host_list, module, arg, sudo, forks)
                else:
                    try:
                        response = await tornado.ioloop.IOLoop.current().run_in_executor(
                            SyncPool, Api.run_cmd, name, host_list, module, arg, sudo, forks)
                        self.finish(response)   # sync task wait for response
                    except Exception as e:
                        self.finish(Tool.jsonal(
                            {'error': str(e), 'rc': ErrorCode.ERRCODE_BIZ}))


class Playbook(Controller):

    async def post(self):
        data = Tool.parsejson(self.request.body)
        Tool.LOGGER.debug("MORE DETAIL: data %s" % data)
        name = data['n'].encode('utf-8').decode()
        hosts = data['h']
        sign = data['s']
        yml_file = data['f'].encode('utf-8').decode()
        mode = data.get('i', False)
        forks = data.get('c', 50)
        if not hosts or not yml_file or not sign:
            self.finish(Tool.jsonal(
                {'error': "Lack of necessary parameters", 'rc': ErrorCode.ERRCODE_SYS}))
        else:
            hotkey = name + hosts + yml_file + Config.Get('sign_key')
            Tool.LOGGER.debug("MORE DETAIL: hot key %s" % hotkey)
            check_str = Tool.getmd5(hotkey)
            if sign != check_str:
                self.finish(Tool.jsonal(
                    {'error': "Sign is error", 'rc': ErrorCode.ERRCODE_BIZ}))
            else:
                myvars = {'hosts': hosts}
                # injection vars in playbook (rule: vars start with "v_" in
                # post data)
                for (k, v) in data.items():
                    if k[0:2] == "v_":
                        myvars[k[2:]] = v
                yml_file = Config.Get('dir_playbook') + yml_file

                Tool.LOGGER.debug("MORE DETAIL: yml file %s" % yml_file)
                if os.path.isfile(yml_file):
                    Tool.LOGGER.info("playbook: {0}, host: {1}, forks: {2}".format(
                        yml_file, hosts, forks))
                    if mode:
                        self.finish(
                            {'rc': ErrorCode.ERRCODE_NONE, 'async': True})  # async execute task release http connection
                        await tornado.ioloop.IOLoop.current().run_in_executor(
                            AsyncPool, Api.run_play_book, name, yml_file, hosts, forks, myvars)
                    else:
                        try:
                            response = await tornado.ioloop.IOLoop.current().run_in_executor(
                                SyncPool, Api.run_play_book, name, yml_file, hosts, forks, myvars)
                        except BaseException as e:
                            Tool.LOGGER.exception('A serious error occurs')
                            self.finish(Tool.jsonal(
                                {'error': str(e), 'rc': ErrorCode.ERRCODE_BIZ}))
                        else:
                            self.finish(response)

                else:
                    self.finish(Tool.jsonal(
                        {'error': "yml file(" + yml_file + ") is not existed", 'rc': ErrorCode.ERRCODE_SYS}))


class FileList(Controller):

    async def get(self):
        path = self.get_argument('type', 'script')
        sign = self.get_argument('sign', '')
        allows = ['script', 'playbook']
        if path in allows:
            hotkey = path + Config.Get('sign_key')
            check_str = Tool.getmd5(hotkey)
            if sign != check_str:
                self.finish(Tool.jsonal(
                    {'error': "Sign is error", 'rc': ErrorCode.ERRCODE_BIZ}))
            else:
                path_var = Config.Get('dir_' + path)
                if os.path.exists(path_var):
                    Tool.LOGGER.info("read file list: " + path_var)
                    dirs = await tornado.ioloop.IOLoop.current().run_in_executor(SyncPool, os.listdir, path_var)
                    self.finish({'list': dirs})
                else:
                    self.finish(Tool.jsonal(
                        {'error': "Path is not existed", 'rc': ErrorCode.ERRCODE_SYS}))
        else:
            self.finish(Tool.jsonal(
                {'error': "Wrong type in argument", 'rc': ErrorCode.ERRCODE_SYS}))


class FileReadWrite(Controller):

    async def get(self):
        path = self.get_argument('type', 'script')
        file_name = self.get_argument('name')
        sign = self.get_argument('sign', '')
        allows = ['script', 'playbook']
        if path in allows:
            hotkey = path + file_name + Config.Get('sign_key')
            check_str = Tool.getmd5(hotkey)
            if sign != check_str:
                self.finish(Tool.jsonal(
                    {'error': "Sign is error", 'rc': ErrorCode.ERRCODE_BIZ}))
            else:
                file_path = Config.Get('dir_' + path) + file_name
                if os.path.isfile(file_path):
                    contents = await tornado.ioloop.IOLoop.current().run_in_executor(SyncPool, self.read_file, file_path)
                    self.finish(Tool.jsonal({'content': contents}))
                else:
                    self.finish(Tool.jsonal(
                        {'error': "No such file in script path", 'rc': ErrorCode.ERRCODE_BIZ}))
        else:
            self.finish(Tool.jsonal(
                {'error': "Wrong type in argument", 'rc': ErrorCode.ERRCODE_SYS}))

    @classmethod
    def read_file(cls, file_path):
        file_object = open(file_path)
        try:
            Tool.LOGGER.info("read from file: " + file_path)
            contents = file_object.read()
        except BaseException:
            Tool.LOGGER.error("failed in reading from file: " + file_path)
            contents = ''
        finally:
            file_object.close()
        return contents

    async def post(self):
        data = Tool.parsejson(self.request.body)
        path = data['p']
        filename = data['f']
        content = data['c'].encode('utf-8').decode()
        sign = data['s']
        if not filename or not content or not sign or path \
                not in ['script', 'playbook']:
            self.finish(Tool.jsonal(
                {'error': "Lack of necessary parameters", 'rc': ErrorCode.ERRCODE_SYS}))
        hotkey = path + filename + Config.Get('sign_key')
        check_str = Tool.getmd5(hotkey)
        if sign != check_str:
            self.finish(Tool.jsonal(
                {'error': "Sign is error", 'rc': ErrorCode.ERRCODE_BIZ}))
        else:
            file_path = Config.Get('dir_' + path) + filename
            result = await tornado.ioloop.IOLoop.current().run_in_executor(SyncPool, self.write_file, file_path, content)
            self.finish(Tool.jsonal({'ret': result}))

    def write_file(self, file_path, content):
        result = True
        try:
            file_object = open(file_path, 'w')
            file_object.write(content)
        except BaseException as err:
            result = False
            Tool.LOGGER.error("failed in writing to file: " + file_path)
        else:
            Tool.LOGGER.info("write to file: " + file_path)
            file_object.close()
        return result


class FileExist(Controller):

    def get(self):
        path = self.get_argument('type', 'script')
        file_name = self.get_argument('name')
        sign = self.get_argument('sign', '')
        allows = ['script', 'playbook']
        if path in allows:
            hotkey = path + file_name + Config.Get('sign_key')
            check_str = Tool.getmd5(hotkey)
            if sign != check_str:
                self.finish(Tool.jsonal(
                    {'error': "Sign is error", 'rc': ErrorCode.ERRCODE_BIZ}))
            else:
                file_path = Config.Get('dir_' + path) + file_name
                Tool.LOGGER.info("file exist? " + file_path)
                if os.path.isfile(file_path):
                    self.finish(Tool.jsonal({'ret': True}))
                else:
                    self.finish(Tool.jsonal({'ret': False}))
        else:
            self.finish(Tool.jsonal(
                {'error': "Wrong type in argument", 'rc': ErrorCode.ERRCODE_SYS}))


class ParseVarsFromFile(Controller):

    async def get(self):
        file_name = self.get_argument('name')
        sign = self.get_argument('sign', '')
        hotkey = file_name + Config.Get('sign_key')
        check_str = Tool.getmd5(hotkey)
        if sign != check_str:
            self.finish(Tool.jsonal(
                {'error': "Sign is error", 'rc': ErrorCode.ERRCODE_BIZ}))
        else:
            file_path = Config.Get('dir_playbook') + file_name
            if os.path.isfile(file_path):
                Tool.LOGGER.info("parse from file: " + file_path)
                var = await tornado.ioloop.IOLoop.current().run_in_executor(SyncPool, self.parse_vars, file_path)
                self.finish({'vars': var})
            else:
                self.finish(Tool.jsonal(
                    {'error': "No such file in script path", 'rc': ErrorCode.ERRCODE_SYS}))

    def parse_vars(self, file_path):
        contents = FileReadWrite.read_file(file_path)
        env = Environment()
        ignore_vars = []
        yamlstream = yaml.load(contents)
        for yamlitem in yamlstream:
            if isinstance(yamlitem, dict) and yamlitem.get('vars_files', []) and len(yamlitem['vars_files']) > 0:
                for vf in yamlitem['vars_files']:
                    tmp_file = Config.Get('dir_playbook') + vf
                    if os.path.isfile(tmp_file):
                        with open(tmp_file, 'r') as fc:
                            tmp_vars = yaml.load(fc)
                            if isinstance(tmp_vars, dict):
                                ignore_vars += tmp_vars.keys()
        if len(ignore_vars) > 0:
            Tool.LOGGER.info("skip vars: " + ",".join(ignore_vars))
        ast = env.parse(contents)
        var = list(meta.find_undeclared_variables(ast))
        var = list(set(var).difference(set(ignore_vars)))
        return var