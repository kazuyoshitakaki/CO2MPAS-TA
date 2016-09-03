#! python
# -*- coding: UTF-8 -*-
#
# Copyright 2015 European Commission (JRC);
# Licensed under the EUPL (the 'Licence');
# You may not use this work except in compliance with the Licence.
# You may obtain a copy of the Licence at: http://ec.europa.eu/idabc/eupl

import logging
import os
import tempfile
from tests._tutils import chdir
import unittest

import ddt
from traitlets.config import get_config

from co2mpas.__main__ import init_logging
from co2mpas.sampling import baseapp, dice, project
import itertools as itt
import os.path as osp
import pandas as pd


init_logging(True)

log = logging.getLogger(__name__)

mydir = osp.dirname(__file__)



@ddt.ddt
class TApp(unittest.TestCase):

    @ddt.data(*list(itt.product((
        dice.MainCmd.document_config_options,
        dice.MainCmd.print_alias_help,
        dice.MainCmd.print_flag_help,
        dice.MainCmd.print_options,
        dice.MainCmd.print_subcommands,
        dice.MainCmd.print_examples,
        dice.MainCmd.print_help,
        ), project.project_subcmds))
    )
    def test_app(self, case):
        meth, cmd_cls = case
        c = get_config()
        c.MainCmd.raise_config_file_errors = True
        cmd = cmd_cls(config=c)
        meth(cmd)



@ddt.ddt
class TProjectStory(unittest.TestCase):
    ## INFO: Must run a whole, ordering of TCs matter.

    @classmethod
    def setUpClass(cls):
        cls._project_repo = tempfile.TemporaryDirectory()
        log.debug('Temp-repo: %s', cls._project_repo)

    @classmethod
    def tearDownClass(cls):
        cls._project_repo.cleanup()

    @property
    def _config(self):
        c = get_config()
        c.Project.repo_path = self._project_repo.name
        c.Spec.verbose = 0
        return c

    def _check_infos_shapes(self, cmd, proj=None):
        res = cmd.proj_examine(project=proj)
        self.assertEqual(len(res), 8, res)

        cmd.verbose = 1
        res = cmd.proj_examine()
        self.assertEqual(len(res), 18, res)

        cmd.verbose = 2
        res = cmd.proj_examine()
        self.assertEqual(len(res), 46, res)

    def test_1a_empty_list(self):
        cmd = project.ProjectCmd.ListCmd(config=self._config)
        res = cmd.run()
        self.assertIsNone(res)

        cmd = project.Project(config=self._config)

        cmd.verbose = 1
        res = cmd.proj_list()
        self.assertIsNone(res)

        cmd.verbose = 2
        res = cmd.proj_list()
        self.assertIsNone(res)

    def test_1b_empty_infos(self):
        cmd = project.ProjectCmd.ExamineCmd(config=self._config)
        res = cmd.run()
        self.assertIsNotNone(res)

        cmd = project.Project(config=self._config)
        self._check_infos_shapes(cmd)

    def test_1b_empty_cwp(self):
        cmd = project.ProjectCmd.CurrentCmd(config=self._config)
        res = cmd.run()
        self.assertEqual(res, '')

    def test_2a_add_project(self):
        cmd = project.ProjectCmd.AddCmd(config=self._config)
        res = cmd.run('foo')
        self.assertIsNone(res)

        cmd = project.ProjectCmd.CurrentCmd(config=self._config)
        res = cmd.run()
        self.assertEqual(res, 'foo')


    def test_2b_list(self):
        cmd = project.ProjectCmd.ListCmd(config=self._config)

        res = cmd.run()
        self.assertEqual(res, ['* foo'])

        cmd = project.Project(config=self._config)

        cmd.verbose = 1
        res = cmd.proj_list()
        self.assertIsInstance(res, pd.DataFrame)
        self.assertEqual(res.shape, (1, 6), res)
        self.assertIn('* foo', str(res))

        cmd.verbose = 2
        res = cmd.proj_list()
        self.assertIsInstance(res, pd.DataFrame)
        self.assertEqual(res.shape, (1, 6), res)
        self.assertIn('* foo', str(res))

    def test_2c_default_infos(self):
        cmd = project.ProjectCmd.ExamineCmd(config=self._config)
        res = cmd.run()
        self.assertRegex(res, 'msg.project += foo')

        cmd = project.Project(config=self._config)
        self._check_infos_shapes(cmd)

    def test_3a_add_same_project__fail(self):
        cmd = project.ProjectCmd.AddCmd(config=self._config)
        with self.assertRaisesRegex(baseapp.CmdException, r"Project 'foo' already exists!"):
            cmd.run('foo')

        cmd = project.ProjectCmd.ListCmd(config=self._config)
        res = list(cmd.run())
        self.assertEqual(res, ['* foo'])

    @ddt.data('sp ace', '%fg', '1ffg')
    def test_3b_add_bad_project__fail(self, proj):
        cmd = project.ProjectCmd.AddCmd(config=self._config)
        with self.assertRaisesRegex(baseapp.CmdException, "Invalid name '%s' for a project!" % proj):
            cmd.run(proj)

        cmd = project.ProjectCmd.ListCmd(config=self._config)
        res = list(cmd.run())
        self.assertEqual(res, ['* foo'])

    def test_4a_add_another_project(self):
        cmd = project.ProjectCmd.AddCmd(config=self._config)
        res = cmd.run('bar')
        self.assertIsNone(res)

        cmd = project.ProjectCmd.CurrentCmd(config=self._config)
        res = cmd.run()
        self.assertEqual(res, 'bar')

    def test_4b_list_projects(self):
        cmd = project.ProjectCmd.ListCmd(config=self._config)
        res = cmd.run()
        self.assertSequenceEqual(res, ['* bar', '  foo'])

        cmd = project.Project(config=self._config)

        cmd.verbose = 1
        res = cmd.proj_list()
        self.assertIsInstance(res, pd.DataFrame)
        self.assertEqual(res.shape, (2, 6), res)
        self.assertIn('* bar', str(res))

        cmd.verbose = 2
        res = cmd.proj_list()
        self.assertIsInstance(res, pd.DataFrame)
        self.assertEqual(res.shape, (2, 6), res)
        self.assertIn('* bar', str(res))

    def test_4c_default_infos(self):
        cmd = project.ProjectCmd.ExamineCmd(config=self._config)
        res = cmd.run()
        self.assertRegex(res, 'msg.project += bar')

        cmd = project.Project(config=self._config)
        self._check_infos_shapes(cmd)

    def test_4d_forced_infos(self):
        cmd = project.ProjectCmd.ExamineCmd(config=self._config)
        res = cmd.run('foo')
        self.assertRegex(res, 'msg.project += bar')

        cmd = project.Project(config=self._config)
        self._check_infos_shapes(cmd, 'foo')

    def test_5_open_other(self):
        cmd = project.ProjectCmd.OpenCmd(config=self._config)
        res = cmd.run('foo')
        self.assertIsNone(res)

        cmd = project.ProjectCmd.CurrentCmd(config=self._config)
        res = cmd.run()
        self.assertEqual(res, 'foo')

        cmd = project.Project(config=self._config)
        self._check_infos_shapes(cmd, 'foo')

    def test_6_open_non_existing(self):
        cmd = project.ProjectCmd.OpenCmd(config=self._config)
        with self.assertRaisesRegex(baseapp.CmdException, "Project 'who' not found!"):
            cmd.run('who')


class TBackupCmd(unittest.TestCase):

    def setUp(self):
        self._project_repo = tempfile.TemporaryDirectory()
        log.debug('Temp-repo: %s', self._project_repo)

    def tearDown(self):
        self._project_repo.cleanup()

    @property
    def _config(self):
        c = get_config()
        c.Project.repo_path = self._project_repo.name
        c.Spec.verbose = 0
        return c

    def test_backup_cwd(self):
        project.ProjectCmd.AddCmd(config=self._config).run('foobar')
        cmd = project.ProjectCmd.BackupCmd(config=self._config)
        with tempfile.TemporaryDirectory() as td:
            with chdir(td):
                res = cmd.run()
                self.assertIn(td, res)
                self.assertIn(os.getcwd(), res)
                self.assertTrue(osp.isfile(res), (res, os.listdir(osp.split(res)[0])))

    def test_backup_fullpath(self):
        project.ProjectCmd.AddCmd(config=self._config).run('foobar')
        cmd = project.ProjectCmd.BackupCmd(config=self._config)
        with tempfile.TemporaryDirectory() as td:
            fpath = osp.join(td, 'foo')
            with chdir(td):
                res = cmd.run(fpath)
                self.assertIn(td, res)
                self.assertIn('foo.txz', res)
                self.assertNotIn('co2mpas', res)
                self.assertTrue(osp.isfile(res), (res, os.listdir(osp.split(res)[0])))

    def test_backup_folder_only(self):
        project.ProjectCmd.AddCmd(config=self._config).run('foobar')
        cmd = project.ProjectCmd.BackupCmd(config=self._config)
        with tempfile.TemporaryDirectory() as td:
            fpath = td +'\\'
            with chdir(td):
                res = cmd.run(fpath)
                self.assertIn(fpath, res)
                self.assertIn('co2mpas', res)
                self.assertTrue(osp.isfile(res), (res, os.listdir(osp.split(res)[0])))

    def test_backup_no_dir(self):
        project.ProjectCmd.AddCmd(config=self._config).run('foobar')
        cmd = project.ProjectCmd.BackupCmd(config=self._config)
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaisesRegex(baseapp.CmdException,
                                        r"Folder '.+__BAD_FOLDER' to store archive does not exist!"):
                cmd.run(osp.join(td, '__BAD_FOLDER', 'foo'))